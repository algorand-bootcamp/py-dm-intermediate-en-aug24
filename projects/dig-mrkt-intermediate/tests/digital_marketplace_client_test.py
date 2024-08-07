import base64
import functools

import algokit_utils
import algosdk.error
import pytest
from algokit_utils import (
    ensure_funded,
    EnsureBalanceParameters,
    TransactionParameters,
)
from algokit_utils.beta.account_manager import AddressAndSigner
from algokit_utils.beta.algorand_client import AlgorandClient
from algokit_utils.beta.composer import (
    AssetCreateParams,
    PayParams,
    AssetTransferParams,
    AssetOptInParams,
)
from algokit_utils.config import config
from algosdk.atomic_transaction_composer import TransactionWithSigner
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient

from smart_contracts.artifacts.digital_marketplace.digital_marketplace_client import (
    DigitalMarketplaceClient,
)


@pytest.fixture(scope="session")
def creator(algorand_client: AlgorandClient) -> AddressAndSigner:
    acct = algorand_client.account.random()

    ensure_funded(
        algorand_client.client.algod,
        EnsureBalanceParameters(
            account_to_fund=acct.address, min_spending_balance_micro_algos=10_000_000
        ),
    )

    return acct


@pytest.fixture(scope="session")
def test_assets_id(
    algorand_client: AlgorandClient,
    creator: AddressAndSigner,
) -> (int, int):
    return (
        algorand_client.send.asset_create(
            AssetCreateParams(sender=creator.address, total=10_000, decimals=3)
        )["confirmation"]["asset-index"],
        algorand_client.send.asset_create(
            AssetCreateParams(sender=creator.address, total=10_000, decimals=3)
        )["confirmation"]["asset-index"],
    )


@pytest.fixture(scope="session")
def buyer(
    algorand_client: AlgorandClient, test_assets_id: (int, int)
) -> AddressAndSigner:
    acct = algorand_client.account.random()

    ensure_funded(
        algorand_client.client.algod,
        EnsureBalanceParameters(
            account_to_fund=acct.address, min_spending_balance_micro_algos=100_000_000
        ),
    )

    for asset_id in test_assets_id:
        algorand_client.send.asset_opt_in(
            AssetOptInParams(
                sender=acct.address,
                asset_id=asset_id,
                signer=acct.signer,
            )
        )

    return acct


@pytest.fixture(scope="session")
def digital_marketplace_client(
    algod_client: AlgodClient, indexer_client: IndexerClient, creator: AddressAndSigner
) -> DigitalMarketplaceClient:
    config.configure(
        debug=True,
        # trace_all=True,
    )

    client = DigitalMarketplaceClient(
        algod_client,
        creator=creator.address,
        signer=creator.signer,
        indexer_client=indexer_client,
    )

    client.create_bare()

    ensure_funded(
        algod_client,
        EnsureBalanceParameters(
            account_to_fund=client.app_address,
            min_spending_balance_micro_algos=0,
        ),
    )

    return client


def test_allow_asset_pass(
    algorand_client: AlgorandClient,
    digital_marketplace_client: DigitalMarketplaceClient,
    creator: AddressAndSigner,
    test_assets_id: (int, int),
) -> None:
    for asset_id in test_assets_id:
        with pytest.raises(algosdk.error.AlgodHTTPError):
            algorand_client.client.algod.account_asset_info(
                digital_marketplace_client.app_address, asset_id
            )

        result = digital_marketplace_client.allow_asset(
            mbr_pay=TransactionWithSigner(
                txn=algorand_client.transactions.payment(
                    PayParams(
                        sender=creator.address,
                        receiver=digital_marketplace_client.app_address,
                        amount=100_000,
                        extra_fee=1_000,
                    )
                ),
                signer=creator.signer,
            ),
            asset=asset_id,
        )

        assert result.confirmed_round

        assert (
            digital_marketplace_client.algod_client.account_asset_info(
                digital_marketplace_client.app_address, asset_id
            )["asset-holding"]["amount"]
            == 0
        )


def test_first_deposit_pass(
    algorand_client: AlgorandClient,
    digital_marketplace_client: DigitalMarketplaceClient,
    creator: AddressAndSigner,
    test_assets_id: (int, int),
) -> None:
    for asset_id in test_assets_id:
        box_key = (
            b"listings"
            + algosdk.encoding.decode_address(creator.address)
            + algosdk.encoding.encode_as_bytes(asset_id)
            + algosdk.encoding.encode_as_bytes(0)
        )
        box_mbr = (
            digital_marketplace_client.compose()
            .get_listings_mbr()
            .simulate()
            .abi_results[0]
            .return_value
        )

        result = digital_marketplace_client.first_deposit(
            mbr_pay=TransactionWithSigner(
                txn=algorand_client.transactions.payment(
                    PayParams(
                        sender=creator.address,
                        receiver=digital_marketplace_client.app_address,
                        amount=box_mbr,
                    )
                ),
                signer=creator.signer,
            ),
            xfer=TransactionWithSigner(
                txn=algorand_client.transactions.asset_transfer(
                    AssetTransferParams(
                        asset_id=asset_id,
                        sender=creator.address,
                        receiver=digital_marketplace_client.app_address,
                        amount=3_000,
                    )
                ),
                signer=creator.signer,
            ),
            unitary_price=1_000_000,
            nonce=0,
            transaction_parameters=TransactionParameters(boxes=[(0, box_key)]),
        )

        assert result.confirmed_round

        box_content = algorand_client.client.algod.application_box_by_name(
            digital_marketplace_client.app_id,
            box_key,
        )["value"]
        decoded_box_content = base64.b64decode(box_content)
        assert int.from_bytes(decoded_box_content[:8], "big") == 3_000
        assert int.from_bytes(decoded_box_content[8:16], "big") == 1_000_000


def test_deposit_pass(
    algorand_client: AlgorandClient,
    digital_marketplace_client: DigitalMarketplaceClient,
    creator: AddressAndSigner,
    test_assets_id: (int, int),
) -> None:
    for asset_id in test_assets_id:
        box_key = (
            b"listings"
            + algosdk.encoding.decode_address(creator.address)
            + algosdk.encoding.encode_as_bytes(asset_id)
            + algosdk.encoding.encode_as_bytes(0)
        )

        result = digital_marketplace_client.deposit(
            xfer=TransactionWithSigner(
                algorand_client.transactions.asset_transfer(
                    AssetTransferParams(
                        asset_id=asset_id,
                        sender=creator.address,
                        receiver=digital_marketplace_client.app_address,
                        amount=1_000,
                    )
                ),
                creator.signer,
            ),
            nonce=0,
            transaction_parameters=algokit_utils.TransactionParameters(
                boxes=[(0, box_key)]
            ),
        )

        assert result.confirmed_round

        box_content = algorand_client.client.algod.application_box_by_name(
            digital_marketplace_client.app_id,
            box_key,
        )["value"]
        decoded_box_content = base64.b64decode(box_content)
        assert int.from_bytes(decoded_box_content[:8], "big") == 4_000
        assert int.from_bytes(decoded_box_content[8:16], "big") == 1_000_000


def test_set_price_pass(
    algorand_client: AlgorandClient,
    digital_marketplace_client: DigitalMarketplaceClient,
    creator: AddressAndSigner,
    test_assets_id: (int, int),
) -> None:
    for asset_id, unitary_price in zip(test_assets_id, [3_200_000, 5_700_000]):
        box_key = (
            b"listings"
            + algosdk.encoding.decode_address(creator.address)
            + algosdk.encoding.encode_as_bytes(asset_id)
            + algosdk.encoding.encode_as_bytes(0)
        )

        result = digital_marketplace_client.set_price(
            asset=asset_id,
            nonce=0,
            unitary_price=unitary_price,
            transaction_parameters=TransactionParameters(boxes=[(0, box_key)]),
        )

        assert result.confirmed_round

        box_content = algorand_client.client.algod.application_box_by_name(
            digital_marketplace_client.app_id,
            box_key,
        )["value"]
        assert (
            int.from_bytes(base64.b64decode(box_content)[8:16], "big") == unitary_price
        )


def test_buy_pass(
    algorand_client: AlgorandClient,
    digital_marketplace_client: DigitalMarketplaceClient,
    buyer: AddressAndSigner,
    creator: AddressAndSigner,
    test_assets_id: (int, int),
) -> None:
    for asset_id, amount_to_pay in zip(test_assets_id, [6_793_600, 12_101_100]):
        box_key = (
            b"listings"
            + algosdk.encoding.decode_address(creator.address)
            + algosdk.encoding.encode_as_bytes(asset_id)
            + algosdk.encoding.encode_as_bytes(0)
        )

        result = digital_marketplace_client.buy(
            owner=creator.address,
            asset=asset_id,
            nonce=0,
            buy_pay=TransactionWithSigner(
                txn=algorand_client.transactions.payment(
                    PayParams(
                        sender=buyer.address,
                        receiver=creator.address,
                        amount=amount_to_pay,
                        extra_fee=1_000,
                    )
                ),
                signer=buyer.signer,
            ),
            quantity=2_123,
            transaction_parameters=TransactionParameters(
                sender=buyer.address,
                signer=buyer.signer,
                boxes=[(0, box_key)],
            ),
        )

        assert result.confirmed_round

        assert (
            algorand_client.client.algod.account_asset_info(buyer.address, asset_id)[
                "asset-holding"
            ]["amount"]
            == 2_123
        )


def test_withdraw_pass(
    algorand_client: AlgorandClient,
    digital_marketplace_client: DigitalMarketplaceClient,
    creator: AddressAndSigner,
    test_assets_id: (int, int),
) -> None:
    for asset_id in test_assets_id:
        box_key = (
            b"listings"
            + algosdk.encoding.decode_address(creator.address)
            + algosdk.encoding.encode_as_bytes(asset_id)
            + algosdk.encoding.encode_as_bytes(0)
        )
        box_mbr = (
            digital_marketplace_client.compose()
            .get_listings_mbr()
            .simulate()
            .abi_results[0]
            .return_value
        )

        before_call_amount = algorand_client.client.algod.account_info(creator.address)[
            "amount"
        ]

        sp = algorand_client.client.algod.suggested_params()
        sp.flat_fee = True
        sp.fee = 3_000
        result = digital_marketplace_client.withdraw(
            asset=asset_id,
            nonce=0,
            transaction_parameters=algokit_utils.TransactionParameters(
                boxes=[(0, box_key)], suggested_params=sp
            ),
        )

        assert result.confirmed_round

        after_call_amount = algorand_client.client.algod.account_info(creator.address)[
            "amount"
        ]

        assert after_call_amount - before_call_amount == box_mbr - 3_000
        assert (
            algorand_client.client.algod.account_asset_info(creator.address, asset_id)[
                "asset-holding"
            ]["amount"]
            == 10_000 - 2_123
        )
