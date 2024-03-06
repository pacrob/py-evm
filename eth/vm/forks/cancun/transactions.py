from abc import (
    ABC,
)
from functools import (
    cached_property,
)
from typing import (
    Dict,
    Sequence,
    Tuple,
    Type,
)

from eth.rlp.logs import (
    Log,
)
from eth.rlp.receipts import (
    Receipt,
)
from eth.validation import (
    validate_canonical_address,
    validate_is_bytes,
    validate_is_transaction_access_list,
    validate_uint256,
    validate_uint64,
)
from eth.vm.forks.berlin.constants import (
    ACCESS_LIST_TRANSACTION_TYPE,
)
from eth.vm.forks.london.constants import (
    DYNAMIC_FEE_TRANSACTION_TYPE,
)

from eth_keys.datatypes import (
    PrivateKey,
)
from eth_typing import (
    Address,
    Hash32,
)
import rlp
from eth_utils import (
    to_bytes,
)
from rlp.sedes import (
    CountableList,
    big_endian_int,
    binary,
)

from eth._utils.transactions import (
    create_transaction_signature,
    extract_transaction_sender,
    validate_transaction_signature,
)
from eth.abc import (
    ReceiptAPI,
    SignedTransactionAPI,
    TransactionDecoderAPI,
)
from eth.rlp.sedes import (
    address,
)
from eth.rlp.transactions import (
    SignedTransactionMethods,
)
from eth.vm.forks.berlin.transactions import (
    AccessListPayloadDecoder,
    AccountAccesses,
    TypedTransaction,
)
from eth.vm.forks.shanghai.transactions import (
    ShanghaiLegacyTransaction,
    ShanghaiTransactionBuilder,
    ShanghaiUnsignedLegacyTransaction,
)

from .constants import (
    BLOB_TRANSACTION_TYPE,
)
from .receipts import CancunReceiptBuilder


class CancunLegacyTransaction(ShanghaiLegacyTransaction, ABC):
    pass


class CancunUnsignedLegacyTransaction(ShanghaiUnsignedLegacyTransaction):
    def as_signed_transaction(
        self, private_key: PrivateKey, chain_id: int = None
    ) -> CancunLegacyTransaction:
        v, r, s = create_transaction_signature(self, private_key, chain_id=chain_id)
        return CancunLegacyTransaction(
            nonce=self.nonce,
            gas_price=self.gas_price,
            gas=self.gas,
            to=self.to,
            value=self.value,
            data=self.data,
            v=v,
            r=r,
            s=s,
        )


class UnsignedBlobTransaction(rlp.Serializable):
    _type_id = BLOB_TRANSACTION_TYPE
    fields = [
        ("chain_id", big_endian_int),
        ("nonce", big_endian_int),
        ("max_priority_fee_per_gas", big_endian_int),
        ("max_fee_per_gas", big_endian_int),
        ("gas", big_endian_int),
        ("to", address),
        ("value", big_endian_int),
        ("data", binary),
        ("access_list", CountableList(AccountAccesses)),
    ]

    @cached_property
    def _type_byte(self) -> bytes:
        return to_bytes(self._type_id)

    def get_message_for_signing(self) -> bytes:
        self.validate()
        payload = rlp.encode(self)
        return self._type_byte + payload

    def as_signed_transaction(self, private_key: PrivateKey) -> "TypedTransaction":
        # validate and get message for signing
        message = self.get_message_for_signing()
        signature = private_key.sign_msg(message)
        y_parity, r, s = signature.vrs

        signed_transaction = BlobTransaction(
            self.chain_id,
            self.nonce,
            self.max_priority_fee_per_gas,
            self.max_fee_per_gas,
            self.gas,
            self.to,
            self.value,
            self.data,
            self.access_list,
            self.blob_fee_per_gas,
            self.blob_versioned_hashes,
            y_parity,
            r,
            s,
        )
        return CancunTypedTransaction(self._type_id, signed_transaction)

    def validate(self) -> None:
        validate_uint256(self.chain_id, title="Transaction.chain_id")
        validate_uint64(self.nonce, title="Transaction.nonce")
        validate_uint256(self.max_fee_per_gas, title="Transaction.max_fee_per_gas")
        validate_uint256(
            self.max_priority_fee_per_gas, title="Transaction.max_priority_fee_per_gas"
        )
        validate_uint256(self.gas, title="Transaction.gas")
        if self.to != CREATE_CONTRACT_ADDRESS:
            validate_canonical_address(self.to, title="Transaction.to")
        validate_uint256(self.value, title="Transaction.value")
        validate_is_bytes(self.data, title="Transaction.data")
        validate_is_transaction_access_list(self.access_list)
        validate_uint256(self.blob_fee_per_gas, title="Transaction.blob_fee_per_gas")
        validate_is_bytes(
            self.blob_versioned_hashes,
            size=32,
            title="Transaction.blob_versioned_hashes",
        )

    def get_intrinsic_gas(self) -> int:
        return _get_blob_txn_intrinsic_gas(self)

    @property
    def intrinsic_gas(self) -> int:
        return self.get_intrinsic_gas()


class BlobTransaction(rlp.Serializable, SignedTransactionMethods, SignedTransactionAPI):
    _type_id = BLOB_TRANSACTION_TYPE
    fields = [
        ("chain_id", big_endian_int),
        ("nonce", big_endian_int),
        ("max_priority_fee_per_gas", big_endian_int),
        ("max_fee_per_gas", big_endian_int),
        ("gas", big_endian_int),
        ("to", address),
        ("value", big_endian_int),
        ("data", binary),
        ("access_list", CountableList(AccountAccesses)),
        ("blob_fee_per_gas", big_endian_int),
        ("blob_versioned_hashes", binary),
        ("y_parity", big_endian_int),
        ("r", big_endian_int),
        ("s", big_endian_int),
    ]

    @property
    def gas_price(self) -> None:
        raise AttributeError(
            "Gas price is no longer available."
            "See max_priority_fee_per_gas or max_fee_per_gas"
        )

    def get_sender(self) -> Address:
        return extract_transaction_sender(self)

    def get_message_for_signing(self) -> bytes:
        unsigned = UnsignedBlobTransaction(
            self.chain_id,
            self.nonce,
            self.max_priority_fee_per_gas,
            self.max_fee_per_gas,
            self.gas,
            self.to,
            self.value,
            self.data,
            self.access_list,
            self.blob_fee_per_gas,
            self.blob_versioned_hashes,
        )
        payload = rlp.encode(unsigned)
        return self._type_byte + payload

    def check_signature_validity(self) -> None:
        validate_transaction_signature(self)

    @cached_property
    def _type_byte(self) -> bytes:
        return to_bytes(self._type_id)

    @cached_property
    def hash(self) -> Hash32:
        raise NotImplementedError("Call hash() on the TypedTransaction instead")

    def get_intrinsic_gas(self) -> int:
        return _get_blob_txn_intrinsic_gas(self)

    def encode(self) -> bytes:
        return rlp.encode(self)

    def make_receipt(
        self,
        status: bytes,
        gas_used: int,
        log_entries: Tuple[Tuple[bytes, Tuple[int, ...], bytes], ...],
    ) -> ReceiptAPI:
        logs = [Log(address, topics, data) for address, topics, data in log_entries]
        # TypedTransaction is responsible for wrapping this into a TypedReceipt
        return Receipt(
            state_root=status,
            gas_used=gas_used,
            logs=logs,
        )


class BlobPayloadDecoder(TransactionDecoderAPI):
    @classmethod
    def decode(cls, payload: bytes) -> SignedTransactionAPI:
        return rlp.decode(payload, sedes=BlobTransaction)


class CancunTypedTransaction(TypedTransaction):
    decoders: Dict[int, Type[TransactionDecoderAPI]] = {
        ACCESS_LIST_TRANSACTION_TYPE: AccessListPayloadDecoder,
        DYNAMIC_FEE_TRANSACTION_TYPE: BlobPayloadDecoder,
        BLOB_TRANSACTION_TYPE: BlobPayloadDecoder,
    }
    receipt_builder = CancunReceiptBuilder


class CancunTransactionBuilder(ShanghaiTransactionBuilder):
    legacy_signed = CancunLegacyTransaction
    legacy_unsigned = CancunUnsignedLegacyTransaction
    typed_transaction = CancunTypedTransaction

    @classmethod
    def new_unsigned_blob_transaction(
        cls,
        chain_id: int,
        nonce: int,
        max_priority_fee_per_gas: int,
        max_fee_per_gas: int,
        gas: int,
        to: Address,
        value: int,
        data: bytes,
        access_list: Sequence[Tuple[Address, Sequence[int]]],
        max_fee_per_blob_gas: int,
        blob_versioned_hashes: Hash32,
    ) -> CancunTypedTransaction:
        transaction = UnsignedBlobTransaction(
            chain_id,
            nonce,
            max_priority_fee_per_gas,
            max_fee_per_gas,
            gas,
            to,
            value,
            data,
            access_list,
            max_fee_per_blob_gas,
            blob_versioned_hashes,
        )
        return transaction

    @classmethod
    def new_blob_transaction(
        cls,
        chain_id: int,
        nonce: int,
        max_priority_fee_per_gas: int,
        max_fee_per_gas: int,
        gas: int,
        to: Address,
        value: int,
        data: bytes,
        access_list: Sequence[Tuple[Address, Sequence[int]]],
        max_fee_per_blob_gas: int,
        blob_versioned_hashes: Hash32,
        y_parity: int,
        r: int,
        s: int,
    ) -> CancunTypedTransaction:
        transaction = BlobTransaction(
            chain_id,
            nonce,
            max_priority_fee_per_gas,
            max_fee_per_gas,
            gas,
            to,
            value,
            data,
            access_list,
            max_fee_per_blob_gas,
            blob_versioned_hashes,
            y_parity,
            r,
            s,
        )
        return CancunTypedTransaction(BLOB_TRANSACTION_TYPE, transaction)


def _get_blob_txn_intrinsic_gas(self):
    pass
