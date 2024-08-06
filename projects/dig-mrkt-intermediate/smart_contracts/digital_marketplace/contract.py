from algopy import (
    ARC4Contract,
    Asset,
    BoxMap,
    Global,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
    subroutine,
    op,
)
from algopy.arc4 import abimethod


class ListingKey(arc4.Struct):
    owner: arc4.Address
    asset: arc4.UInt64
    nonce: arc4.UInt64


class ListingValue(arc4.Struct):
    deposited: arc4.UInt64
    unitary_price: arc4.UInt64


class DigitalMarketplace(ARC4Contract):
    def __init__(self) -> None:
        self.listings = BoxMap(ListingKey, ListingValue)

    @subroutine
    def listings_box_mbr(self) -> UInt64:
        return (
            2_500
            + (
                # fmt: off
                # Key length
                self.listings.key_prefix.length +
                32 + 8 + 8 +
                # Value length
                8 + 8
                # fmt: on
            )
            * 400
        )

    @abimethod(readonly=True)
    def get_listings_mbr(self) -> UInt64:
        return self.listings_box_mbr()

    @abimethod
    def allow_asset(self, mbr_pay: gtxn.PaymentTransaction, asset: Asset) -> None:
        assert not Global.current_application_address.is_opted_in(asset)

        assert mbr_pay.receiver == Global.current_application_address
        assert mbr_pay.amount == Global.asset_opt_in_min_balance

        itxn.AssetTransfer(
            xfer_asset=asset,
            asset_receiver=Global.current_application_address,
            asset_amount=0,
        ).submit()

    @abimethod
    def first_deposit(
        self,
        mbr_pay: gtxn.PaymentTransaction,
        xfer: gtxn.AssetTransferTransaction,
        unitary_price: arc4.UInt64,
        nonce: arc4.UInt64,
    ) -> None:
        assert mbr_pay.sender == Txn.sender
        assert mbr_pay.receiver == Global.current_application_address
        assert mbr_pay.amount == self.listings_box_mbr()

        key = ListingKey(
            owner=arc4.Address(Txn.sender),
            asset=arc4.UInt64(xfer.xfer_asset.id),
            nonce=nonce,
        )
        assert key not in self.listings

        assert xfer.sender == Txn.sender
        assert xfer.asset_receiver == Global.current_application_address
        assert xfer.asset_amount > 0

        self.listings[key] = ListingValue(
            deposited=arc4.UInt64(xfer.asset_amount), unitary_price=unitary_price
        )

    @abimethod
    def deposit(self, xfer: gtxn.AssetTransferTransaction, nonce: arc4.UInt64) -> None:
        key = ListingKey(
            owner=arc4.Address(Txn.sender),
            asset=arc4.UInt64(xfer.xfer_asset.id),
            nonce=nonce,
        )

        assert xfer.sender == Txn.sender
        assert xfer.asset_receiver == Global.current_application_address
        assert xfer.asset_amount > 0

        self.listings[key].deposited = arc4.UInt64(
            self.listings[key].deposited.native + xfer.asset_amount
        )

    @abimethod
    def set_price(
        self, asset: Asset, nonce: arc4.UInt64, unitary_price: arc4.UInt64
    ) -> None:
        key = ListingKey(
            owner=arc4.Address(Txn.sender),
            asset=arc4.UInt64(asset.id),
            nonce=nonce,
        )

        self.listings[key].unitary_price = unitary_price

    @abimethod
    def buy(
        self,
        owner: arc4.Address,
        asset: Asset,
        nonce: arc4.UInt64,
        buy_pay: gtxn.PaymentTransaction,
        quantity: UInt64,
    ) -> None:
        key = ListingKey(
            owner=owner,
            asset=arc4.UInt64(asset.id),
            nonce=nonce,
        )

        listing = self.listings[key].copy()

        amount_not_scaled_high, amount_not_scaled_low = op.mulw(
            listing.unitary_price.native, quantity
        )
        scaling_factor_high, scaling_factor_low = op.expw(10, asset.decimals)
        _quotient_high, amount_to_be_paid, _remainder_high, _remainder_low = op.divmodw(
            amount_not_scaled_high,
            amount_not_scaled_low,
            scaling_factor_high,
            scaling_factor_low,
        )
        assert not _quotient_high

        assert buy_pay.sender == Txn.sender
        assert buy_pay.receiver.bytes == owner.bytes
        assert buy_pay.amount == amount_to_be_paid

        self.listings[key].deposited = arc4.UInt64(listing.deposited.native - quantity)

        itxn.AssetTransfer(
            xfer_asset=asset,
            asset_receiver=Txn.sender,
            asset_amount=quantity,
        ).submit()

    @abimethod
    def withdraw(self, asset: Asset, nonce: arc4.UInt64) -> None:
        key = ListingKey(
            owner=arc4.Address(Txn.sender),
            asset=arc4.UInt64(asset.id),
            nonce=nonce,
        )

        listing = self.listings[key].copy()

        del self.listings[key]

        itxn.Payment(receiver=Txn.sender, amount=self.listings_box_mbr()).submit()

        itxn.AssetTransfer(
            xfer_asset=asset,
            asset_receiver=Txn.sender,
            asset_amount=listing.deposited.native,
        ).submit()
