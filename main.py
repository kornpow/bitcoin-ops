#!/usr/bin/env python3
"""
Bitcoin OP_RETURN Transaction Creator
Creates and signs Bitcoin testnet transactions with OP_RETURN outputs
"""

import os
import sys
import argparse
import requests
from requests.exceptions import (
    RequestException,
    ConnectionError as RequestsConnectionError,
)
from typing import Any, Optional, Tuple, List, Dict
from embit import script, ec
from embit.networks import NETWORKS
from embit.transaction import Transaction, TransactionInput, TransactionOutput, Witness
from embit.psbt import PSBT
from embit.finalizer import finalize_psbt


class WalletManager:
    """Manages wallet key generation, loading, and persistence"""

    def __init__(self, wallet_file: str = "wallet.key", network_name: str = "test"):
        self.wallet_file = wallet_file
        self.network = NETWORKS[network_name]
        self.priv_key: Optional[ec.PrivateKey] = None
        self.pub_key: Optional[ec.PublicKey] = None
        self.address: Optional[str] = None

    def load_or_generate_key(self) -> Tuple[ec.PrivateKey, ec.PublicKey, str]:
        """Load existing key or generate new one and save to filesystem"""
        if os.path.exists(self.wallet_file):
            print(f"✓ Loading existing wallet from {self.wallet_file}")
            self.priv_key = self._load_key()
        else:
            print(f"✓ Generating new wallet and saving to {self.wallet_file}")
            self.priv_key = self._generate_and_save_key()

        self.pub_key = self.priv_key.get_public_key()
        script_pubkey = script.p2wpkh(self.pub_key)
        address = script_pubkey.address(network=self.network)
        if address is None:
            print("✗ Failed to derive address from public key")
            sys.exit(1)
        self.address = address

        return self.priv_key, self.pub_key, self.address

    def _load_key(self) -> ec.PrivateKey:
        """Load private key from wallet file"""
        try:
            with open(self.wallet_file, "r") as f:
                wif = f.read().strip()

            if not wif:
                raise ValueError("Wallet file is empty")

            priv_key = ec.PrivateKey.from_wif(wif)
            return priv_key
        except Exception as e:
            print(f"✗ Error loading wallet: {e}")
            sys.exit(1)

    def _generate_and_save_key(self) -> ec.PrivateKey:
        """Generate new private key and save to filesystem"""
        try:
            # Generate random 256-bit private key
            privdata = os.urandom(32)
            priv_key = ec.PrivateKey(privdata)

            # Convert to WIF format for storage
            wif = priv_key.wif(network=self.network)

            # Save to file with restricted permissions
            with open(self.wallet_file, "w") as f:
                f.write(wif)

            # Set file permissions to read/write for owner only (Unix-like systems)
            if hasattr(os, "chmod"):
                os.chmod(self.wallet_file, 0o600)

            print(f"✓ Private key saved to {self.wallet_file}")
            print("⚠️  IMPORTANT: Keep this file secure! It contains your private key.")

            return priv_key
        except Exception as e:
            print(f"✗ Error generating/saving wallet: {e}")
            sys.exit(1)


class UTXOManager:
    """Manages UTXO fetching and validation"""

    def __init__(
        self,
        network_name: str = "test",
        rpc_url: Optional[str] = None,
        use_rpc: bool = False,
        rpc_only: bool = False,
    ):
        self.network_name = network_name
        self.use_rpc = use_rpc
        self.rpc_url = rpc_url
        self.rpc_only = rpc_only

        if network_name == "test":
            self.api_base = "https://mempool.space/testnet/api"
        else:
            self.api_base = "https://mempool.space/api"

    def _rpc_call(self, method: str, params: List, timeout: int = 10) -> Any:
        """Make an RPC call to Bitcoin Core"""
        if not self.rpc_url:
            return None

        try:
            payload = {
                "jsonrpc": "1.0",
                "id": "bitcoin-ops",
                "method": method,
                "params": params,
            }
            response = requests.post(self.rpc_url, json=payload, timeout=timeout)

            if response.status_code == 200:
                result = response.json()
                if result.get("error"):
                    # Don't print error here, let caller handle it
                    return None
                return result.get("result")
            return None
        except RequestException:
            return None

    def _print_txindex_warning(self) -> None:
        """Print warning message about txindex not being enabled"""
        print("\n✗ ERROR: txindex is not enabled on your Bitcoin Core node")
        print("\n  This tool requires txindex=1 when using RPC mode.")
        print("\n  To enable txindex:")
        print("    1. Add 'txindex=1' to your bitcoin.conf")
        print("    2. Restart Bitcoin Core (it will reindex the blockchain)")
        print("    3. Wait for reindexing to complete (may take several hours)")
        print("\n  Or run without RPC flags to use mempool.space API only")
        print(
            "\n  Alternatively, use --rpc-only for slow scantxoutset mode (no txindex needed)"
        )

    def check_txindex_enabled(self) -> bool:
        """Check if txindex is enabled on the Bitcoin Core node"""
        if not self.rpc_url:
            return False

        # Try to get blockchain info which includes txindex status
        result = self._rpc_call("getblockchaininfo", [])
        if result and "blocks" in result:
            # Node is accessible, check if we can fetch a non-coinbase transaction
            # Use a known testnet transaction (not genesis/coinbase which can't be fetched)
            # This is one of the user's successful OP_RETURN transactions from history
            test_txid = (
                "63611617ee33c761c2c9586d0f998baa16bfd876b703921a6a1b31c2933abf64"
            )
            test_result = self._rpc_call("getrawtransaction", [test_txid, False])

            if test_result:
                return True
            else:
                return False
        return False

    def fetch_utxos(self, address: str) -> List[Dict]:
        """Fetch all UTXOs for an address from RPC or mempool.space API"""
        if self.use_rpc and self.rpc_url:
            return self._fetch_utxos_rpc(address)
        else:
            return self._fetch_utxos_api(address)

    def _fetch_utxos_rpc(self, address: str) -> List[Dict]:
        """Fetch UTXOs using Bitcoin Core RPC"""
        try:
            if self.rpc_only:
                # Use scantxoutset to find UTXOs (RPC only mode)
                print("  Using Bitcoin Core RPC only (scantxoutset)...")
                print(
                    "  ⚠️  Warning: This may take 30-60 seconds to scan the UTXO set..."
                )

                # Use scantxoutset to find UTXOs for this address
                result = self._rpc_call(
                    "scantxoutset",
                    ["start", [f"addr({address})"]],
                    timeout=120,  # Increase timeout for scantxoutset
                )

                if not result:
                    print("  ✗ scantxoutset failed or returned no results")
                    return []

                # Convert scantxoutset format to our format
                utxos = []
                for unspent in result.get("unspents", []):
                    utxos.append(
                        {
                            "txid": unspent["txid"],
                            "vout": unspent["vout"],
                            "value": int(
                                unspent["amount"] * 100_000_000
                            ),  # BTC to sats
                            "status": {
                                "confirmed": True
                            },  # scantxoutset only returns confirmed
                        }
                    )

                print(f"  ✓ Found {len(utxos)} UTXOs via scantxoutset")
                return utxos
            else:
                # Hybrid mode: use mempool.space for discovery, RPC for verification
                print(
                    "  Using Bitcoin Core RPC (hybrid mode with mempool.space for discovery)..."
                )

                # Note: We use mempool.space to find UTXOs since scantxoutset is slow
                # and listunspent requires the address to be imported into the wallet.
                # For transaction fetching and broadcasting, we'll still use RPC.
                print("  → Getting UTXO list from mempool.space (faster)...")
                utxos = self._fetch_utxos_api(address)

                # If we found UTXOs via API, verify they exist via RPC
                if utxos and self.rpc_url:
                    print("  → Verifying UTXOs with local node...")
                    verified_utxos = []
                    for utxo in utxos:
                        # Try to get the transaction via RPC to verify it exists locally
                        tx_result = self._rpc_call(
                            "getrawtransaction", [utxo["txid"], True]
                        )
                        if tx_result:
                            # UTXO exists in local node
                            verified_utxos.append(utxo)

                    if verified_utxos:
                        print(
                            f"  ✓ Verified {len(verified_utxos)}/{len(utxos)} UTXOs exist in local node"
                        )
                        return verified_utxos
                    else:
                        print(
                            "  ⚠️  Warning: UTXOs not found in local node (may not be fully synced)"
                        )
                        print("  Using mempool.space data anyway...")

                return utxos

        except Exception as e:
            print(f"  ✗ RPC error: {e}")
            if not self.rpc_only:
                print("  Falling back to mempool.space API only...")
                return self._fetch_utxos_api(address)
            return []

    def _fetch_utxos_api(self, address: str) -> List[Dict]:
        """Fetch all UTXOs for an address from mempool.space API"""
        try:
            print("  Using mempool.space API...")
            url = f"{self.api_base}/address/{address}/utxo"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            print(f"✗ Error fetching UTXOs: {e}")
            return []

    def fetch_transaction(self, txid: str) -> Optional[Transaction]:
        """Fetch transaction by txid from RPC or API"""
        if self.use_rpc and self.rpc_url:
            return self._fetch_transaction_rpc(txid)
        else:
            return self._fetch_transaction_api(txid)

    def _fetch_transaction_rpc(self, txid: str) -> Optional[Transaction]:
        """Fetch transaction using Bitcoin Core RPC (requires txindex=1)"""
        try:
            result = self._rpc_call("getrawtransaction", [txid, False])
            if result:
                return Transaction.parse(bytes.fromhex(result))

            # If RPC failed, txindex is likely not enabled
            print(
                f"\n✗ ERROR: Could not fetch transaction {txid[:16]}... from local node"
            )
            print(
                "  This usually means txindex is not enabled in your Bitcoin Core node."
            )
            print("\n  To enable txindex:")
            print("    1. Add 'txindex=1' to your bitcoin.conf")
            print("    2. Restart Bitcoin Core (it will reindex the blockchain)")
            print("    3. Wait for reindexing to complete")
            print("\n  Or run without RPC flags to use mempool.space API only")
            return None
        except Exception as e:
            print(f"\n✗ RPC transaction fetch failed: {e}")
            print("  Make sure txindex=1 is enabled in bitcoin.conf")
            return None

    def _fetch_transaction_api(self, txid: str) -> Optional[Transaction]:
        """Fetch transaction by txid from mempool.space API"""
        try:
            url = f"{self.api_base}/tx/{txid}/hex"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            # Parse hex transaction string into Transaction object
            return Transaction.parse(bytes.fromhex(response.text.strip()))
        except RequestException as e:
            print(f"✗ Error fetching transaction: {e}")
            return None
        except Exception as e:
            print(f"✗ Error parsing transaction: {e}")
            return None

    def display_utxos(self, utxos: List[Dict]) -> None:
        """Display available UTXOs in a formatted way"""
        if not utxos:
            print("No UTXOs found for this address.")
            return

        print(f"\nAvailable UTXOs ({len(utxos)} found):")
        print("-" * 80)
        for i, utxo in enumerate(utxos):
            btc_value = utxo["value"] / 100_000_000
            print(f"[{i}] TXID: {utxo['txid']}")
            print(f"    VOUT: {utxo['vout']}")
            print(f"    Amount: {utxo['value']} sats ({btc_value:.8f} BTC)")
            print(
                f"    Status: {'Confirmed' if utxo.get('status', {}).get('confirmed') else 'Unconfirmed'}"
            )
            print()


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
        op_return_data_list: List[bytes],
        prev_tx: Transaction,
        witness_data: Optional[bytes] = None,
    ) -> Transaction:
        """Create a transaction with optional OP_RETURN and/or P2WSH witness data outputs."""

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

        if fee < 1:
            fee = 1

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
        witness_data: Optional[bytes] = None,
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
        extra_utxo: Optional[Dict] = None,
        extra_prev_tx: Optional[Transaction] = None,
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


def main():
    parser = argparse.ArgumentParser(
        description="Create Bitcoin transactions on testnet: OP_RETURN or witness data storage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check wallet balance
  python main.py -b

  # Store data via OP_RETURN (immediately readable, ≤80 bytes)
  python main.py --op-return "Hello Bitcoin!" -x

  # Store a file via OP_RETURN
  python main.py --op-return --file poem.txt -x

  # Store large data (up to 10KB) via P2WSH witness script
  python main.py --witness "my data here" -x
  python main.py --witness --file data.bin -x

  # Reveal witness data on-chain by spending the P2WSH output
  python main.py --spend TXID:VOUT --witness --file data.bin -x

  # Custom fee rate
  python main.py --op-return "hi" --fee-rate 5 -x

  # Use environment variable for wallet location
  export BITCOIN_OPS_WALLET=~/wallets/testnet.key
  python main.py -b

Environment Variables:
  BITCOIN_OPS_WALLET    Path to wallet file (overrides --wallet-file)
        """,
    )

    parser.add_argument(
        "--wallet-file",
        default="wallet.key",
        help="Path to wallet key file (default: wallet.key, supports ~ expansion)",
    )
    parser.add_argument(
        "--network",
        default="test",
        choices=["test", "main"],
        help="Bitcoin network (default: test)",
    )

    # --- Data mode flags ---
    parser.add_argument(
        "--op-return",
        dest="op_return",
        type=str,
        nargs="?",  # 0 or 1 value: --op-return "text" OR --op-return (uses --file)
        const="",  # present with no value → use --file
        action="append",
        metavar="TEXT",
        help="Store data via OP_RETURN output (≤80 bytes; use multiple times for multiple outputs). "
        "Alias: --data",
    )
    parser.add_argument(
        "--data",  # legacy alias, same dest
        dest="op_return",
        type=str,
        action="append",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--witness",
        dest="witness_text",
        type=str,
        nargs="?",
        const="",  # present with no value → use --file
        metavar="TEXT",
        help="Store arbitrary data (up to 10KB) via P2WSH witness script. "
        "Pass text inline or use --file. Aliases: --witness-data",
    )
    parser.add_argument(
        "--witness-data",  # legacy alias
        dest="witness_text",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--file",
        dest="file",
        type=str,
        metavar="PATH",
        help="File to read data from. Used with --op-return or --witness.",
    )
    parser.add_argument(
        "--witness-file",  # legacy alias
        dest="file",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--spend",
        dest="spend_p2wsh",
        type=str,
        metavar="TXID:VOUT",
        help="Spend a P2WSH witness-data output, revealing its data on-chain. "
        "Requires --witness or --file to reconstruct the witness script. "
        "Alias: --spend-p2wsh",
    )
    parser.add_argument(
        "--spend-p2wsh",  # legacy alias
        dest="spend_p2wsh",
        type=str,
        help=argparse.SUPPRESS,
    )

    # --- Common flags ---
    parser.add_argument(
        "--fee-rate",
        type=float,
        default=2.0,
        help="Fee rate in sat/vB (default: 2.0, supports fractional rates like 0.5)",
    )
    parser.add_argument(
        "-b",
        "--balance",
        dest="check_balance",
        action="store_true",
        help="Check wallet balance and available UTXOs. Alias: --check-balance",
    )
    parser.add_argument(
        "--check-balance",  # legacy alias
        dest="check_balance",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Show all historical OP_RETURN transactions from this wallet",
    )
    parser.add_argument(
        "--utxo",
        dest="utxo_index",
        type=int,
        metavar="N",
        help="Index of UTXO to use (if multiple available). Alias: --utxo-index",
    )
    parser.add_argument(
        "--utxo-index",  # legacy alias
        dest="utxo_index",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--large",
        dest="allow_large_opreturn",
        action="store_true",
        help="Allow OP_RETURN data >80 bytes (may not relay on standard nodes). "
        "Alias: --allow-large-opreturn",
    )
    parser.add_argument(
        "--allow-large-opreturn",  # legacy alias
        dest="allow_large_opreturn",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-x",
        "--broadcast",
        dest="broadcast",
        action="store_true",
        help="Automatically broadcast transaction to mempool.space",
    )

    # --- RPC flags (unchanged) ---
    parser.add_argument(
        "--rpc-url",
        type=str,
        help="Bitcoin Core RPC URL (e.g., http://user:pass@localhost:8332)",
    )
    parser.add_argument("--rpc-user", type=str, help="Bitcoin Core RPC username")
    parser.add_argument("--rpc-password", type=str, help="Bitcoin Core RPC password")
    parser.add_argument(
        "--rpc-host",
        type=str,
        default="localhost",
        help="Bitcoin Core RPC host (default: localhost)",
    )
    parser.add_argument(
        "--rpc-port",
        type=int,
        help="Bitcoin Core RPC port (default: 8332 for mainnet, 18332 for testnet)",
    )
    parser.add_argument(
        "--rpc-only",
        action="store_true",
        help="Use ONLY local Bitcoin Core RPC (scantxoutset for UTXO discovery, slower but no external API)",
    )

    args = parser.parse_args()

    # Resolve wallet file path - support environment variable and expand paths
    wallet_file = os.getenv("BITCOIN_OPS_WALLET", args.wallet_file)
    wallet_file = os.path.expanduser(wallet_file)  # Expand ~ to home directory
    wallet_file = os.path.abspath(wallet_file)  # Convert to absolute path

    # Create parent directory if it doesn't exist
    wallet_dir = os.path.dirname(wallet_file)
    if wallet_dir and not os.path.exists(wallet_dir):
        try:
            os.makedirs(wallet_dir, mode=0o700)
            print(f"✓ Created wallet directory: {wallet_dir}")
        except OSError as e:
            print(f"✗ Error creating wallet directory: {e}")
            return

    # Initialize wallet
    print("=" * 80)
    print("Bitcoin OP_RETURN Transaction Creator")
    print("=" * 80)

    wallet_mgr = WalletManager(wallet_file, args.network)
    priv_key, pub_key, address = wallet_mgr.load_or_generate_key()

    print(f"\n{'Testnet' if args.network == 'test' else 'Mainnet'} Address: {address}")
    print("=" * 80)

    # Build RPC URL if credentials provided
    rpc_url = None
    use_rpc = False
    rpc_only = args.rpc_only
    if args.rpc_url:
        rpc_url = args.rpc_url
        use_rpc = True
    elif args.rpc_user and args.rpc_password:
        # Default ports
        if args.rpc_port:
            port = args.rpc_port
        else:
            port = 18332 if args.network == "test" else 8332
        rpc_url = f"http://{args.rpc_user}:{args.rpc_password}@{args.rpc_host}:{port}"
        use_rpc = True

    # Initialize UTXO manager
    utxo_mgr = UTXOManager(
        args.network, rpc_url=rpc_url, use_rpc=use_rpc, rpc_only=rpc_only
    )

    # If using RPC, check that txindex is enabled
    if use_rpc and not rpc_only:
        print("\n⌛ Checking Bitcoin Core configuration...")
        if not utxo_mgr.check_txindex_enabled():
            utxo_mgr._print_txindex_warning()
            return
        print("  ✓ txindex is enabled")

    # Check if history mode
    if args.history:
        print("\n⌛ Fetching transaction history...")

        try:
            # Fetch all transactions for this address
            if args.network == "test":
                api_base = "https://mempool.space/testnet/api"
            else:
                api_base = "https://mempool.space/api"

            # Get address transactions
            tx_url = f"{api_base}/address/{address}/txs"
            response = requests.get(tx_url, timeout=10)
            response.raise_for_status()
            transactions = response.json()

            # Filter for transactions with OP_RETURN outputs
            op_return_txs = []
            for tx in transactions:
                for vout in tx.get("vout", []):
                    if vout.get("scriptpubkey_type") == "op_return":
                        op_return_txs.append(
                            {
                                "txid": tx["txid"],
                                "status": tx.get("status", {}),
                                "vout": vout,
                                "fee": tx.get("fee", 0),
                                "size": tx.get("size", 0),
                            }
                        )
                        break  # Only count each tx once

            if not op_return_txs:
                print("\n📝 No OP_RETURN transactions found for this address")
                return

            print(f"\n📜 Found {len(op_return_txs)} OP_RETURN transaction(s):")
            print("=" * 80)

            for i, tx in enumerate(op_return_txs):
                confirmed = tx["status"].get("confirmed", False)
                block_height = tx["status"].get("block_height", "N/A")

                print(f"\n[{i + 1}] TXID: {tx['txid']}")
                print(f"    Status: {'✓ Confirmed' if confirmed else '⌛ Unconfirmed'}")
                if confirmed:
                    print(f"    Block: {block_height}")
                print(f"    Fee: {tx['fee']} sats")
                print(f"    Size: {tx['size']} bytes")

                # Decode OP_RETURN data
                script_hex = tx["vout"]["scriptpubkey"]
                try:
                    # Skip OP_RETURN opcode (6a) and get the data
                    # Handle different push opcodes
                    if script_hex[:2] == "6a":
                        script_bytes = bytes.fromhex(script_hex)

                        # Determine data location based on push opcode
                        if len(script_bytes) > 1:
                            second_byte = script_bytes[1]

                            if second_byte <= 75:
                                # Direct push (0x01-0x4b)
                                data_start = 2
                                data_len = second_byte
                            elif second_byte == 0x4C:
                                # OP_PUSHDATA1
                                data_start = 3
                                data_len = script_bytes[2]
                            elif second_byte == 0x4D:
                                # OP_PUSHDATA2
                                data_start = 4
                                data_len = int.from_bytes(script_bytes[2:4], "little")
                            else:
                                # Unknown format
                                data_start = 2
                                data_len = len(script_bytes) - 2

                            data = script_bytes[data_start : data_start + data_len]

                            # Try to decode as UTF-8
                            try:
                                decoded = data.decode("utf-8")
                                print(f'    Data: "{decoded}"')
                            except UnicodeDecodeError:
                                print(f"    Data (hex): {data.hex()}")

                            print(f"    Data length: {len(data)} bytes")
                except Exception as e:
                    print(f"    Data: (could not decode: {e})")

                # Link to view on mempool.space
                if args.network == "test":
                    print(f"    View: https://mempool.space/testnet/tx/{tx['txid']}")
                else:
                    print(f"    View: https://mempool.space/tx/{tx['txid']}")

            print("\n" + "=" * 80)
            print(f"Total OP_RETURN transactions: {len(op_return_txs)}")

        except RequestException as e:
            print(f"\n✗ Error fetching transaction history: {e}")

        return

    # Fetch UTXOs
    print("\n⌛ Fetching UTXOs...")
    utxos = utxo_mgr.fetch_utxos(address)
    utxo_mgr.display_utxos(utxos)

    if not utxos:
        print("\n⚠️  No funds available!")
        print("\n📝 To get testnet coins, visit a faucet:")
        print("   • https://testnet-faucet.mempool.co/")
        print("   • https://coinfaucet.eu/en/btc-testnet/")
        print(f"\n   Send coins to: {address}")
        return

    # If just checking balance, exit here
    if args.check_balance:
        total = sum(u["value"] for u in utxos)
        print(f"\n💰 Total balance: {total} sats ({total / 100_000_000:.8f} BTC)")
        return

    # Handle witness data: --witness "text" or --witness --file path or --witness-data (legacy)
    witness_data = None
    if args.witness_text and args.witness_text != "":
        witness_data = args.witness_text.encode("utf-8")
    elif args.file and not args.op_return:
        # --file without --op-return → witness mode
        try:
            with open(os.path.expanduser(args.file), "rb") as f:
                witness_data = f.read()
        except Exception as e:
            print(f"✗ Error reading file: {e}")
            return
    elif args.witness_text == "" and args.file:
        # --witness --file path
        try:
            with open(os.path.expanduser(args.file), "rb") as f:
                witness_data = f.read()
        except Exception as e:
            print(f"✗ Error reading file: {e}")
            return

    # Validate witness data size
    if witness_data is not None:
        if len(witness_data) > 10240:
            print(
                f"✗ ERROR: Witness data too large ({len(witness_data)} bytes, max 10KB)"
            )
            return
        print(
            f"\n✓ Witness data loaded: {len(witness_data)} bytes (stored in witness, not OP_RETURN)"
        )

    # Resolve --op-return --file path (OP_RETURN from file)
    op_return_list = list(args.op_return) if args.op_return else []
    if args.file and args.op_return is not None:
        # Replace any empty sentinel ("") entries with file content
        file_text = None
        for i, val in enumerate(op_return_list):
            if val == "":
                if file_text is None:
                    try:
                        with open(os.path.expanduser(args.file), "r") as f:
                            file_text = f.read()
                    except Exception as e:
                        print(f"✗ Error reading file: {e}")
                        return
                op_return_list[i] = file_text

    # Need data to create transaction
    if not op_return_list and not witness_data and not args.spend_p2wsh:
        print(
            "\n⚠️  Use --op-return for OP_RETURN, --witness for witness storage, or -b to view balance"
        )
        return

    # Handle --spend: spend an existing P2WSH witness-data output
    if args.spend_p2wsh:
        if witness_data is None:
            print(
                "✗ ERROR: --spend requires --witness or --file to reconstruct the witness script"
            )
            return

        try:
            parts = args.spend_p2wsh.split(":")
            if len(parts) != 2:
                raise ValueError("expected TXID:VOUT")
            p2wsh_txid = parts[0]
            p2wsh_vout = int(parts[1])
        except ValueError as e:
            print(f"✗ ERROR: Invalid --spend value ({e}). Expected format: TXID:VOUT")
            return

        # Fetch the P2WSH output amount from the API
        print(f"\n⌛ Fetching P2WSH output {p2wsh_txid[:16]}...:{p2wsh_vout}...")
        prev_tx = utxo_mgr.fetch_transaction(p2wsh_txid)
        if not prev_tx:
            print("✗ Failed to fetch P2WSH transaction")
            return
        p2wsh_amount = prev_tx.vout[p2wsh_vout].value
        print(f"  Amount: {p2wsh_amount} sats")

        # If P2WSH output alone can't cover fee + dust change, consolidate with
        # the first available P2WPKH UTXO to ensure a valid change output
        extra_utxo = None
        extra_prev_tx = None
        if p2wsh_amount < 800:  # rough threshold: fee ~270 + dust 546
            if utxos:
                extra_utxo = utxos[0]
                print(
                    f"\n  P2WSH output ({p2wsh_amount} sats) too small for fee+change alone."
                )
                print(
                    f"  Consolidating with UTXO {extra_utxo['txid'][:16]}...:{extra_utxo['vout']} ({extra_utxo['value']} sats)"
                )
                extra_prev_tx = utxo_mgr.fetch_transaction(extra_utxo["txid"])
                if not extra_prev_tx:
                    print("✗ Failed to fetch consolidation UTXO transaction")
                    return
            else:
                print(
                    "⚠️  P2WSH output too small for fee+change and no P2WPKH UTXOs available to consolidate."
                )

        print("\n⌛ Building P2WSH spending transaction...")
        builder = OPReturnTransactionBuilder(wallet_mgr, fee_rate=args.fee_rate)
        final_tx = builder.spend_p2wsh_transaction(
            p2wsh_txid,
            p2wsh_vout,
            p2wsh_amount,
            witness_data,
            extra_utxo=extra_utxo,
            extra_prev_tx=extra_prev_tx,
        )

        print("\n" + "=" * 80)
        print("✓ P2WSH spending transaction created!")
        print("  The full witness script (with embedded data) is in the witness field.")
        print("=" * 80)
        tx_hex = final_tx.to_string()
        print(f"\nTransaction Hex:\n{tx_hex}")
        print("\n" + "=" * 80)

        if args.broadcast:
            if args.network == "test":
                broadcast_url = "https://mempool.space/testnet/api/tx"
            else:
                broadcast_url = "https://mempool.space/api/tx"
            print("\n⌛ Broadcasting to mempool.space...")
            try:
                response = requests.post(broadcast_url, data=tx_hex, timeout=10)
                if response.status_code == 200:
                    txid = response.text.strip()
                    print("\n✓ Broadcast successful!")
                    print(f"  TXID: {txid}")
                    if args.network == "test":
                        print(f"  View: https://mempool.space/testnet/tx/{txid}")
                    else:
                        print(f"  View: https://mempool.space/tx/{txid}")
                else:
                    print(
                        f"\n✗ Broadcast failed! Status {response.status_code}: {response.text}"
                    )
            except RequestException as e:
                print(f"\n✗ Network error during broadcast: {e}")
        else:
            if args.network == "test":
                print(
                    "\n📡 To broadcast: run with -x, or paste hex at https://mempool.space/testnet/tx/push"
                )
            else:
                print(
                    "\n📡 To broadcast: run with -x, or paste hex at https://mempool.space/tx/push"
                )
        return

    # Select UTXO
    if args.utxo_index is not None:
        if args.utxo_index >= len(utxos):
            print(f"✗ Invalid UTXO index. Available: 0-{len(utxos) - 1}")
            return
        selected_utxo = utxos[args.utxo_index]
    else:
        # Use first (largest) UTXO
        selected_utxo = utxos[0]

    print(f"\n✓ Using UTXO: {selected_utxo['txid']}:{selected_utxo['vout']}")
    print(f"  Amount: {selected_utxo['value']} sats")

    # Fetch previous transaction
    print("\n⌛ Fetching previous transaction...")
    prev_tx = utxo_mgr.fetch_transaction(selected_utxo["txid"])
    if not prev_tx:
        print("✗ Failed to fetch previous transaction")
        return

    # Build and sign transaction
    print("\n⌛ Building transaction...")
    builder = OPReturnTransactionBuilder(wallet_mgr, fee_rate=args.fee_rate)

    # Convert all data strings to bytes
    op_return_data_list = (
        [d.encode("utf-8") for d in op_return_list] if op_return_list else []
    )

    # Display all OP_RETURN data
    if op_return_data_list:
        print(
            f"✓ Creating transaction with {len(op_return_data_list)} OP_RETURN output(s):"
        )
        total_data_size = 0
        for i, data in enumerate(op_return_data_list):
            print(f'  [{i + 1}] Data: "{op_return_list[i]}"')
            print(f"      Bytes: {data.hex()}")
            print(f"      Length: {len(data)} bytes")
            total_data_size += len(data)

        print(f"\n  Total OP_RETURN data size: {total_data_size} bytes")
    else:
        print("✓ Creating transaction without OP_RETURN data")

    if witness_data:
        print(f"✓ Witness data included: {len(witness_data)} bytes")

    # Warn about multiple OP_RETURN outputs
    if len(op_return_data_list) > 1:
        print(
            f"\n⚠️  WARNING: Transaction has {len(op_return_data_list)} OP_RETURN outputs"
        )
        print(
            "  Bitcoin Core's default policy rejects multiple OP_RETURN outputs (multi-op-return)"
        )
        print("  This is a STANDARDNESS rule, not a consensus rule.")
        print("\n  This transaction will NOT propagate on the standard network!")
        print("\n  To broadcast, you need a custom Bitcoin Core node with:")
        print("    -datacarriersize=<size>  (for total data size)")
        print("    -permitbaremultisig=1    (allows multiple OP_RETURN)")
        print("\n  Note: Most explorers and nodes will reject this transaction.")

    # Check size limits for each OP_RETURN
    max_data_size = 0
    if op_return_data_list:
        max_data_size = max(len(d) for d in op_return_data_list)
        if max_data_size > 80 and not args.allow_large_opreturn:
            print(
                f"\n✗ ERROR: One or more OP_RETURN outputs exceed 80 bytes (largest: {max_data_size} bytes)"
            )
            print(
                "\n  Bitcoin Core's default policy (-datacarriersize=80) rejects OP_RETURN >80 bytes"
            )
            print(
                "  Most nodes and services (including mempool.space) won't relay these transactions"
            )
            print("\n  Solutions:")
            print("    1. Shorten your messages to ≤80 bytes each")
            print("    2. Use --large flag (transaction may not broadcast)")
            print("    3. Broadcast to a node with higher -datacarriersize setting")
            return

        if max_data_size > 80:
            print(
                f"⚠️  WARNING: One or more OP_RETURN outputs exceed 80 bytes (largest: {max_data_size} bytes)"
            )
            print("  This transaction likely won't relay on standard nodes!")
            print(
                f"  You'll need to broadcast to a custom node with -datacarriersize={max_data_size} or higher"
            )

        if max_data_size > 10000:
            print(f"\n✗ ERROR: OP_RETURN data is too large ({max_data_size} bytes)")
            print("  Maximum reasonable size is around 10KB")
            return

    tx = builder.create_transaction(
        selected_utxo["txid"],
        selected_utxo["vout"],
        selected_utxo["value"],
        op_return_data_list,
        prev_tx,
        witness_data=witness_data,
    )

    print("⌛ Signing transaction...")
    final_tx = builder.sign_transaction(
        tx,
        selected_utxo["txid"],
        selected_utxo["vout"],
        prev_tx.vout[selected_utxo["vout"]],
        witness_data=witness_data,
    )

    print("\n" + "=" * 80)
    print("✓ Transaction created successfully!")
    print("=" * 80)

    tx_hex = final_tx.to_string()
    print(f"\nTransaction Hex:\n{tx_hex}")
    print("\n" + "=" * 80)

    # Broadcast if requested
    if args.broadcast or args.rpc_url or args.rpc_user:
        # Determine broadcast method
        use_rpc = args.rpc_url or args.rpc_user

        if use_rpc:
            # Broadcast to local Bitcoin Core node via RPC
            print("\n⌛ Broadcasting transaction to Bitcoin Core RPC...")

            # Build RPC URL
            port: int = (
                args.rpc_port
                if args.rpc_port
                else (18332 if args.network == "test" else 8332)
            )
            if args.rpc_url:
                rpc_url = args.rpc_url
            else:
                # Construct from components
                if not args.rpc_user or not args.rpc_password:
                    print("✗ ERROR: RPC user and password required")
                    print(
                        "  Use --rpc-user and --rpc-password, or provide full --rpc-url"
                    )
                    return

                rpc_url = (
                    f"http://{args.rpc_user}:{args.rpc_password}@{args.rpc_host}:{port}"
                )

            try:
                # Make RPC call to sendrawtransaction
                rpc_payload = {
                    "jsonrpc": "1.0",
                    "id": "bitcoin-ops",
                    "method": "sendrawtransaction",
                    "params": [tx_hex],
                }

                response = requests.post(rpc_url, json=rpc_payload, timeout=10)

                if response.status_code == 200:
                    result = response.json()

                    if "error" in result and result["error"]:
                        print(f"\n✗ RPC error: {result['error']}")

                        # Provide helpful error messages
                        error_msg = str(result["error"])
                        if "bad-txns-inputs-missingorspent" in error_msg:
                            print("  This usually means the UTXO was already spent")
                        elif "min relay fee" in error_msg:
                            print("  Transaction fee is too low, increase --fee-rate")
                        elif (
                            "scriptpubkey" in error_msg
                            or "mandatory-script-verify-flag" in error_msg
                        ):
                            print(
                                "  Script validation failed - check OP_RETURN data size"
                            )
                    else:
                        txid = result.get("result", "")
                        print("\n✓ Transaction broadcast successful via RPC!")
                        print(f"  TXID: {txid}")

                        if args.network == "test":
                            print("\n  View on mempool.space:")
                            print(f"  https://mempool.space/testnet/tx/{txid}")
                        else:
                            print("\n  View on mempool.space:")
                            print(f"  https://mempool.space/tx/{txid}")
                else:
                    print("\n✗ RPC request failed!")
                    print(f"  Status code: {response.status_code}")
                    print(f"  Response: {response.text}")

            except RequestsConnectionError:
                print("\n✗ Connection error: Could not connect to Bitcoin Core RPC")
                print(
                    f"  URL: {rpc_url.replace(args.rpc_password if args.rpc_password else '', '****') if args.rpc_password else rpc_url}"
                )
                print("\n  Make sure:")
                print("    1. Bitcoin Core is running")
                print("    2. RPC server is enabled (server=1 in bitcoin.conf)")
                print("    3. Credentials are correct")
                print(f"    4. RPC port is correct ({port})")

            except RequestException as e:
                print(f"\n✗ Network error during RPC broadcast: {e}")

        else:
            # Broadcast to mempool.space
            print("\n⌛ Broadcasting transaction to mempool.space...")

            if args.network == "test":
                broadcast_url = "https://mempool.space/testnet/api/tx"
            else:
                broadcast_url = "https://mempool.space/api/tx"
                if not args.allow_large_opreturn or max_data_size <= 80:
                    # Extra confirmation for mainnet
                    print("⚠️  WARNING: This will broadcast to MAINNET (real Bitcoin)!")
                    confirm = input("Type 'yes' to confirm: ")
                    if confirm.lower() != "yes":
                        print("Broadcast cancelled")
                        return

            try:
                response = requests.post(broadcast_url, data=tx_hex, timeout=10)

                if response.status_code == 200:
                    txid = response.text.strip()
                    print("\n✓ Transaction broadcast successful!")
                    print(f"  TXID: {txid}")

                    if args.network == "test":
                        print("\n  View on mempool.space:")
                        print(f"  https://mempool.space/testnet/tx/{txid}")
                    else:
                        print("\n  View on mempool.space:")
                        print(f"  https://mempool.space/tx/{txid}")
                else:
                    print("\n✗ Broadcast failed!")
                    print(f"  Status code: {response.status_code}")
                    print(f"  Response: {response.text}")

                    # Try to parse error message
                    try:
                        error_msg = response.text
                        if "scriptpubkey" in error_msg.lower():
                            print(
                                "\n  This error usually means the OP_RETURN data is too large (>80 bytes)"
                            )
                            print(f"  Largest OP_RETURN is {max_data_size} bytes")
                    except Exception:
                        pass

            except RequestException as e:
                print(f"\n✗ Network error during broadcast: {e}")
    else:
        # Show manual broadcast instructions
        if args.network == "test":
            print("\n📡 Broadcast options:")
            print("   • Run with -x to use mempool.space")
            print("   • Run with --rpc-user/--rpc-password to use local Bitcoin Core")
            print(
                "   • Or manually paste hex at: https://mempool.space/testnet/tx/push"
            )
        else:
            print("\n⚠️  MAINNET TRANSACTION - Verify carefully before broadcasting!")
            print("\n📡 Broadcast options:")
            print("   • Run with -x to use mempool.space")
            print("   • Run with --rpc-user/--rpc-password to use local Bitcoin Core")
            print("   • Or manually paste hex at: https://mempool.space/tx/push")


if __name__ == "__main__":
    main()
