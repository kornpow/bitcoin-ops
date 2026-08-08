"""Transaction construction and signing."""

from embit import script
from embit.finalizer import finalize_psbt
from embit.psbt import PSBT
from embit.transaction import Transaction, TransactionInput, TransactionOutput, Witness

from .wallet import WalletManager


class OPReturnTransactionBuilder:
    """Builds and signs OP_RETURN transactions"""

    def __init__(self, wallet_manager: WalletManager, fee_rate: float = 2.0):
        self.wallet = wallet_manager
        self.network = wallet_manager.network
        self.fee_rate = fee_rate  # satoshis per vbyte (can be fractional)

    def _create_op_return_script(self, data: bytes) -> script.Script:
        """Create an OP_RETURN script from data bytes"""
        # OP_RETURN opcode is 0x6a
        # For data <= 75 bytes: OP_RETURN <push_length> <data>
        # For data 76-255 bytes: OP_RETURN OP_PUSHDATA1 <length> <data>
        # For data 256-65535 bytes: OP_RETURN OP_PUSHDATA2 <length_2bytes_LE> <data>
        if len(data) <= 75:
            op_return_script_bytes = bytes([0x6A, len(data)]) + data
        elif len(data) <= 255:
            # OP_PUSHDATA1 (0x4c) for data 76-255 bytes
            op_return_script_bytes = bytes([0x6A, 0x4C, len(data)]) + data
        elif len(data) <= 65535:
            # OP_PUSHDATA2 (0x4d) for data 256-65535 bytes
            # Length is encoded as 2 bytes in little-endian
            length_bytes = len(data).to_bytes(2, byteorder="little")
            op_return_script_bytes = bytes([0x6A, 0x4D]) + length_bytes + data
        else:
            raise ValueError(f"OP_RETURN data too large: {len(data)} bytes (max 65535)")

        return script.Script(op_return_script_bytes)

    def create_witness_script(self, data: bytes) -> script.Script:
        """Create a P2WSH witness script that embeds arbitrary data.

        The witness script is: <data> OP_DROP <pubkey> OP_CHECKSIG
        This is a valid spendable script - the data is pushed then dropped,
        and the remaining key + checksig behaves like P2PK.

        Spending requires witness stack: [<sig>, <witness_script>]
        """
        if self.wallet.pub_key is None:
            raise RuntimeError(
                "Wallet public key not loaded - call load_or_generate_key() first"
            )
        pub_sec = self.wallet.pub_key.sec()  # 33-byte compressed pubkey
        script_bytes = (
            self._make_push(data)
            + bytes([0x75])
            + self._make_push(pub_sec)
            + bytes([0xAC])
        )
        # 0x75 = OP_DROP, 0xAC = OP_CHECKSIG
        return script.Script(script_bytes)

    def _make_push(self, data: bytes) -> bytes:
        """Return minimal script push encoding for data."""
        n = len(data)
        if n == 0:
            return bytes([0x00])  # OP_0
        elif n <= 75:
            return bytes([n]) + data
        elif n <= 255:
            return bytes([0x4C, n]) + data  # OP_PUSHDATA1
        elif n <= 65535:
            return bytes([0x4D]) + n.to_bytes(2, "little") + data  # OP_PUSHDATA2
        else:
            raise ValueError(f"Data too large to push: {n} bytes (max 65535)")

    def create_transaction(
        self,
        utxo_txid: str,
        utxo_vout: int,
        utxo_amount: int,
        op_return_data_list: list[bytes],
        prev_tx: Transaction,
        witness_data: bytes | None = None,
    ) -> Transaction:
        """Create a transaction with optional OP_RETURN and/or P2WSH witness data outputs."""

        if self.fee_rate <= 0:
            raise ValueError("fee rate must be greater than zero")
        if len(utxo_txid) != 64:
            raise ValueError("UTXO transaction ID must be 64 hexadecimal characters")
        try:
            bytes.fromhex(utxo_txid)
        except ValueError as exc:
            raise ValueError("UTXO transaction ID must be hexadecimal") from exc
        if utxo_vout < 0 or utxo_vout >= len(prev_tx.vout):
            raise IndexError("UTXO output index is outside the previous transaction")
        if prev_tx.vout[utxo_vout].value != utxo_amount:
            raise ValueError(
                "UTXO amount does not match the previous transaction output"
            )

        # Create transaction
        tx = Transaction(version=2, locktime=0)

        # Add input
        txin = TransactionInput(bytes.fromhex(utxo_txid), utxo_vout)
        tx.vin.append(txin)

        # Add OP_RETURN outputs
        total_op_return_size = 0
        for op_return_data in op_return_data_list:
            op_return_script = self._create_op_return_script(op_return_data)
            op_return_output = TransactionOutput(
                value=0, script_pubkey=op_return_script
            )
            tx.vout.append(op_return_output)
            total_op_return_size += 10 + len(
                op_return_data
            )  # ~10 bytes overhead per output

        # Add P2WSH output for witness data storage
        # P2WSH dust limit is 330 sats; value=0 is rejected by relay policy
        P2WSH_DUST = 330
        if witness_data is not None:
            witness_script_obj = self.create_witness_script(witness_data)
            p2wsh_script = script.p2wsh(witness_script_obj)
            p2wsh_output = TransactionOutput(
                value=P2WSH_DUST, script_pubkey=p2wsh_script
            )
            tx.vout.append(p2wsh_output)

        # Calculate estimated vsize and fee
        # Base tx overhead: 10 bytes (non-witness) + segwit marker/flag: 0.5 vbytes
        # P2WPKH input: 41 bytes non-witness + (1+1+73+33)/4 = ~27 vbytes witness = ~68 vbytes total
        # OP_RETURN output: 9 + script_len bytes (non-witness)
        # P2WSH output: 9 + 34 = 43 bytes (non-witness, fixed)
        # Change output (P2WPKH): 31 bytes
        # For the P2WPKH input witness: (1 + 73 + 33) = 107 witness bytes -> 107/4 = ~27 vbytes
        p2wsh_output_size = 43 if witness_data is not None else 0
        estimated_vsize = 10 + 68 + total_op_return_size + p2wsh_output_size + 31
        fee = int(self.fee_rate * estimated_vsize)

        fee = max(fee, 1)

        # Calculate change: subtract fee and the sats locked in P2WSH output
        p2wsh_locked = P2WSH_DUST if witness_data is not None else 0
        change_amount = utxo_amount - fee - p2wsh_locked

        if change_amount < 546:  # Dust limit
            print(
                f"⚠️  Warning: Change amount ({change_amount} sats) is below dust limit."
            )
            print(
                f"    Total fee will be {utxo_amount - p2wsh_locked} sats instead of {fee} sats"
            )
            # Don't add change output, all goes to fee
        else:
            # Add change output
            change_script = script.p2wpkh(self.wallet.pub_key)
            change_output = TransactionOutput(
                value=change_amount, script_pubkey=change_script
            )
            tx.vout.append(change_output)

        return tx

    def sign_transaction(
        self,
        tx: Transaction,
        utxo_txid: str,
        utxo_vout: int,
        prev_output: TransactionOutput,
        witness_data: bytes | None = None,
    ) -> Transaction:
        """Sign a P2WPKH input spending transaction using PSBT.

        If witness_data is provided, the transaction also contains a P2WSH output
        (value=0) whose witness script embeds the data. That output is unspendable
        at zero value but the data is permanently stored in the witness script hash.
        The input being spent is always a standard P2WPKH, so signing is unchanged.
        """
        # Create PSBT
        psbt = PSBT(tx)

        # Set witness UTXO info for the P2WPKH input
        psbt.inputs[0].witness_utxo = prev_output

        # Sign with private key (embit handles segwit sighash internally for P2WPKH)
        psbt.sign_with(self.wallet.priv_key)

        # Finalize the PSBT -> produces the signed transaction with witness stack
        final_tx = finalize_psbt(psbt)

        if final_tx is None:
            raise RuntimeError(
                "Failed to finalize PSBT - transaction may be missing signatures"
            )

        return final_tx

    def spend_p2wsh_transaction(
        self,
        p2wsh_txid: str,
        p2wsh_vout: int,
        p2wsh_amount: int,
        witness_data: bytes,
        extra_utxo: dict | None = None,
        extra_prev_tx: Transaction | None = None,
    ) -> Transaction:
        """Spend a P2WSH witness-data output, revealing the witness script on-chain.

        The witness script is <data> OP_DROP <pubkey> OP_CHECKSIG.
        Spending requires witness stack: [<sig>, <witness_script>]
        The full witness script (containing all the embedded data) appears in the
        spending transaction's witness field, making it immediately readable on-chain.

        Optionally consolidates with an extra P2WPKH UTXO to ensure enough sats
        for a valid change output above dust.
        """
        if self.wallet.pub_key is None or self.wallet.priv_key is None:
            raise RuntimeError("Wallet not loaded - call load_or_generate_key() first")

        # Reconstruct the witness script from the data
        witness_script_obj = self.create_witness_script(witness_data)
        witness_script_bytes = witness_script_obj.data

        # Build the P2WSH scriptPubKey so we can set it as the witness_utxo
        p2wsh_script = script.p2wsh(witness_script_obj)
        p2wsh_prev_output = TransactionOutput(
            value=p2wsh_amount, script_pubkey=p2wsh_script
        )

        # Create the spending transaction
        tx = Transaction(version=2, locktime=0)

        # Input 0: the P2WSH output being spent (reveals the data)
        txin_p2wsh = TransactionInput(bytes.fromhex(p2wsh_txid), p2wsh_vout)
        tx.vin.append(txin_p2wsh)

        total_input = p2wsh_amount

        # Input 1 (optional): extra P2WPKH UTXO to top up funds for change
        extra_prev_output = None
        if extra_utxo and extra_prev_tx:
            txin_extra = TransactionInput(
                bytes.fromhex(extra_utxo["txid"]), extra_utxo["vout"]
            )
            tx.vin.append(txin_extra)
            extra_prev_output = extra_prev_tx.vout[extra_utxo["vout"]]
            total_input += extra_utxo["value"]

        # Fee estimation:
        # Non-witness base: 10 (overhead) + 41 (P2WSH input) + 41*extra_inputs + 31 (change output)
        # Witness: P2WSH input witness = varint(2) + varint(sig_len) + sig(~72) + varint(ws_len) + ws
        #          P2WPKH input witness = varint(2) + varint(73) + sig(72) + varint(33) + pubkey(33) = 108 bytes each
        ws_len = len(witness_script_bytes)
        ws_len_varint = 3 if ws_len > 255 else (2 if ws_len > 75 else 1)
        p2wsh_witness_weight = 2 + 1 + 72 + ws_len_varint + ws_len
        p2wpkh_witness_weight = 108  # per extra input
        n_extra = 1 if extra_utxo else 0
        # vsize = ceil((base_weight * 3 + total_weight) / 4)
        # simplified: non_witness + witness/4
        non_witness_size = 10 + 41 + 41 * n_extra + 31
        witness_size = p2wsh_witness_weight + p2wpkh_witness_weight * n_extra
        estimated_vsize = non_witness_size + int(witness_size / 4) + 1
        fee = max(1, int(self.fee_rate * estimated_vsize))

        change_amount = total_input - fee
        if change_amount < 546:
            print(
                f"⚠️  Warning: Change ({change_amount} sats) below dust limit — all goes to fee"
            )
        else:
            change_script = script.p2wpkh(self.wallet.pub_key)
            tx.vout.append(
                TransactionOutput(value=change_amount, script_pubkey=change_script)
            )

        # If no outputs (change below dust), we must have at least one —
        # add a minimal OP_RETURN to satisfy the non-empty vout requirement.
        # But also ensure non-witness base size >= 82 bytes for relay.
        if not tx.vout:
            # Adding P2WPKH output at 0 value gets us above 82 non-witness bytes
            # and is the simplest valid output; the value is effectively burned as fee.
            change_script = script.p2wpkh(self.wallet.pub_key)
            tx.vout.append(TransactionOutput(value=0, script_pubkey=change_script))
            print(
                f"  Note: {total_input - fee} sats burned as fee (below dust threshold)"
            )

        # Sign via PSBT
        psbt = PSBT(tx)
        psbt.inputs[0].witness_utxo = p2wsh_prev_output
        psbt.inputs[0].witness_script = witness_script_obj

        if extra_prev_output:
            psbt.inputs[1].witness_utxo = extra_prev_output

        psbt.sign_with(self.wallet.priv_key)

        # Manually assemble witness for input 0 (custom P2WSH): [sig, witness_script]
        sig_bytes = None
        for pub, sig in psbt.inputs[0].partial_sigs.items():
            sig_bytes = sig
            break
        if sig_bytes is None:
            raise RuntimeError(
                "Signing failed — no partial signature produced for P2WSH input"
            )

        final_tx = Transaction.parse(tx.serialize())
        final_tx.vin[0].witness = Witness([sig_bytes, witness_script_bytes])

        # Input 1 is standard P2WPKH — finalize_psbt handles it, but we built
        # the tx manually, so assemble its witness from partial_sigs too
        if extra_prev_output:
            p2wpkh_sig = None
            p2wpkh_pub = None
            for pub, sig in psbt.inputs[1].partial_sigs.items():
                p2wpkh_sig = sig
                p2wpkh_pub = pub.sec()
                break
            if p2wpkh_sig is None:
                raise RuntimeError(
                    "Signing failed — no partial signature for P2WPKH input"
                )
            final_tx.vin[1].witness = Witness([p2wpkh_sig, p2wpkh_pub])

        return final_tx

    def _create_op_return_script_legacy(self, data: bytes) -> script.Script:
        """Alias kept for internal clarity - delegates to _create_op_return_script."""
        return self._create_op_return_script(data)

    # Legacy helpers kept for backward compatibility
    def create_witness_data_script(self, data: bytes) -> script.Script:
        """Deprecated alias for create_witness_script."""
        return self.create_witness_script(data)

    def _create_push_script(self, data: bytes) -> bytes:
        """Deprecated alias for _make_push."""
        return self._make_push(data)
