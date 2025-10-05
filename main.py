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
from typing import Optional, Tuple, List, Dict
from embit import script, ec
from embit.networks import NETWORKS
from embit.transaction import Transaction, TransactionInput, TransactionOutput
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
        self.address = script_pubkey.address(network=self.network)

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

    def __init__(self, network_name: str = "test"):
        self.network_name = network_name
        if network_name == "test":
            self.api_base = "https://mempool.space/testnet/api"
        else:
            self.api_base = "https://mempool.space/api"

    def fetch_utxos(self, address: str) -> List[Dict]:
        """Fetch all UTXOs for an address from mempool.space API"""
        try:
            url = f"{self.api_base}/address/{address}/utxo"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            print(f"✗ Error fetching UTXOs: {e}")
            return []

    def fetch_transaction(self, txid: str) -> Optional[Transaction]:
        """Fetch transaction by txid"""
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

    def __init__(self, wallet_manager: WalletManager, fee_rate: int = 2):
        self.wallet = wallet_manager
        self.network = wallet_manager.network
        self.fee_rate = fee_rate  # satoshis per vbyte

    def create_transaction(
        self,
        utxo_txid: str,
        utxo_vout: int,
        utxo_amount: int,
        op_return_data: bytes,
        prev_tx: Transaction,
    ) -> Transaction:
        """Create an OP_RETURN transaction"""

        # Create transaction
        tx = Transaction(version=2, locktime=0)

        # Add input
        txin = TransactionInput(bytes.fromhex(utxo_txid), utxo_vout)
        tx.vin.append(txin)

        # Add OP_RETURN output
        # OP_RETURN opcode is 0x6a
        # For data <= 75 bytes: OP_RETURN <push_length> <data>
        # For data 76-255 bytes: OP_RETURN OP_PUSHDATA1 <length> <data>
        # For data 256-65535 bytes: OP_RETURN OP_PUSHDATA2 <length_2bytes_LE> <data>
        if len(op_return_data) <= 75:
            op_return_script_bytes = bytes([0x6A, len(op_return_data)]) + op_return_data
        elif len(op_return_data) <= 255:
            # OP_PUSHDATA1 (0x4c) for data 76-255 bytes
            op_return_script_bytes = (
                bytes([0x6A, 0x4C, len(op_return_data)]) + op_return_data
            )
        elif len(op_return_data) <= 65535:
            # OP_PUSHDATA2 (0x4d) for data 256-65535 bytes
            # Length is encoded as 2 bytes in little-endian
            length_bytes = len(op_return_data).to_bytes(2, byteorder="little")
            op_return_script_bytes = bytes([0x6A, 0x4D]) + length_bytes + op_return_data
        else:
            raise ValueError(
                f"OP_RETURN data too large: {len(op_return_data)} bytes (max 65535)"
            )

        op_return_script = script.Script(op_return_script_bytes)
        op_return_output = TransactionOutput(value=0, script_pubkey=op_return_script)
        tx.vout.append(op_return_output)

        # Calculate estimated size and fee
        # Base size: ~10 bytes overhead + input (~68 bytes for P2WPKH) + outputs
        # OP_RETURN output: ~10 + len(data)
        # Change output: ~31 bytes for P2WPKH
        estimated_vsize = 10 + 68 + (10 + len(op_return_data)) + 31
        fee = self.fee_rate * estimated_vsize

        # Calculate change
        change_amount = utxo_amount - fee

        if change_amount < 546:  # Dust limit
            print(
                f"⚠️  Warning: Change amount ({change_amount} sats) is below dust limit."
            )
            print(f"    Total fee will be {utxo_amount} sats instead of {fee} sats")
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
    ) -> Transaction:
        """Sign transaction using PSBT"""

        # Create PSBT
        psbt = PSBT(tx)

        # Set witness UTXO info for P2WPKH (segwit) inputs
        psbt.inputs[0].witness_utxo = prev_output

        # Sign with private key (embit handles the sighash internally)
        psbt.sign_with(self.wallet.priv_key)

        # Finalize the PSBT (creates the witness structure)
        # Note: finalize_psbt returns a NEW transaction object with witnesses
        final_tx = finalize_psbt(psbt)

        if final_tx is None:
            raise RuntimeError(
                "Failed to finalize PSBT - transaction may be missing signatures"
            )

        return final_tx


def main():
    parser = argparse.ArgumentParser(
        description="Create Bitcoin OP_RETURN transactions on testnet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show wallet address and check for funds
  python main.py --check-balance

  # Create OP_RETURN with custom data
  python main.py --data "Hello Bitcoin!"

  # Use specific UTXO (if you have multiple)
  python main.py --data "My message" --utxo-index 0

  # Specify custom fee rate
  python main.py --data "Important data" --fee-rate 3
        """,
    )

    parser.add_argument(
        "--wallet-file",
        default="wallet.key",
        help="Path to wallet key file (default: wallet.key)",
    )
    parser.add_argument(
        "--network",
        default="test",
        choices=["test", "main"],
        help="Bitcoin network (default: test)",
    )
    parser.add_argument("--data", type=str, help="Data to include in OP_RETURN output")
    parser.add_argument(
        "--fee-rate", type=int, default=2, help="Fee rate in sat/vB (default: 2)"
    )
    parser.add_argument(
        "--check-balance",
        action="store_true",
        help="Check wallet balance and available UTXOs",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Show all historical OP_RETURN transactions from this wallet",
    )
    parser.add_argument(
        "--utxo-index", type=int, help="Index of UTXO to use (if multiple available)"
    )
    parser.add_argument(
        "--allow-large-opreturn",
        action="store_true",
        help="Allow OP_RETURN data >80 bytes (may not relay on standard nodes)",
    )
    parser.add_argument(
        "--broadcast",
        action="store_true",
        help="Automatically broadcast transaction to mempool.space",
    )
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

    args = parser.parse_args()

    # Initialize wallet
    print("=" * 80)
    print("Bitcoin OP_RETURN Transaction Creator")
    print("=" * 80)

    wallet_mgr = WalletManager(args.wallet_file, args.network)
    priv_key, pub_key, address = wallet_mgr.load_or_generate_key()

    print(f"\n{'Testnet' if args.network == 'test' else 'Mainnet'} Address: {address}")
    print("=" * 80)

    # Initialize UTXO manager
    utxo_mgr = UTXOManager(args.network)

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

    # Need data to create transaction
    if not args.data:
        print(
            "\n⚠️  Use --data to specify OP_RETURN data, or --check-balance to view funds"
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

    op_return_data = args.data.encode("utf-8")
    print(f'✓ OP_RETURN data: "{args.data}"')
    print(f"  Bytes: {op_return_data.hex()}")
    print(f"  Length: {len(op_return_data)} bytes")

    if len(op_return_data) > 80 and not args.allow_large_opreturn:
        print(f"\n✗ ERROR: OP_RETURN data is {len(op_return_data)} bytes (>80 bytes)")
        print(
            "\n  Bitcoin Core's default policy (-datacarriersize=80) rejects OP_RETURN >80 bytes"
        )
        print(
            "  Most nodes and services (including mempool.space) won't relay these transactions"
        )
        print("\n  Solutions:")
        print("    1. Shorten your message to ≤80 bytes")
        print("    2. Use --allow-large-opreturn flag (transaction may not broadcast)")
        print("    3. Broadcast to a node with higher -datacarriersize setting")
        return

    if len(op_return_data) > 80:
        print(f"⚠️  WARNING: OP_RETURN data is {len(op_return_data)} bytes (>80 bytes)")
        print("  This transaction likely won't relay on standard nodes!")
        print(
            f"  You'll need to broadcast to a custom node with -datacarriersize={len(op_return_data)} or higher"
        )

    if len(op_return_data) > 10000:
        print(f"\n✗ ERROR: OP_RETURN data is too large ({len(op_return_data)} bytes)")
        print("  Maximum reasonable size is around 10KB")
        return

    tx = builder.create_transaction(
        selected_utxo["txid"],
        selected_utxo["vout"],
        selected_utxo["value"],
        op_return_data,
        prev_tx,
    )

    print("⌛ Signing transaction...")
    final_tx = builder.sign_transaction(
        tx,
        selected_utxo["txid"],
        selected_utxo["vout"],
        prev_tx.vout[selected_utxo["vout"]],
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

                # Default ports
                if args.rpc_port:
                    port = args.rpc_port
                else:
                    port = 18332 if args.network == "test" else 8332

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
                print(
                    f"    4. RPC port is correct ({port if 'port' in locals() else 'check your config'})"
                )

            except RequestException as e:
                print(f"\n✗ Network error during RPC broadcast: {e}")

        else:
            # Broadcast to mempool.space
            print("\n⌛ Broadcasting transaction to mempool.space...")

            if args.network == "test":
                broadcast_url = "https://mempool.space/testnet/api/tx"
            else:
                broadcast_url = "https://mempool.space/api/tx"
                if not args.allow_large_opreturn or len(op_return_data) <= 80:
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
                            print(f"  Your data is {len(op_return_data)} bytes")
                    except Exception:
                        pass

            except RequestException as e:
                print(f"\n✗ Network error during broadcast: {e}")
    else:
        # Show manual broadcast instructions
        if args.network == "test":
            print("\n📡 Broadcast options:")
            print("   • Run with --broadcast flag to use mempool.space")
            print("   • Run with --rpc-user/--rpc-password to use local Bitcoin Core")
            print(
                "   • Or manually paste hex at: https://mempool.space/testnet/tx/push"
            )
        else:
            print("\n⚠️  MAINNET TRANSACTION - Verify carefully before broadcasting!")
            print("\n📡 Broadcast options:")
            print("   • Run with --broadcast flag to use mempool.space")
            print("   • Run with --rpc-user/--rpc-password to use local Bitcoin Core")
            print("   • Or manually paste hex at: https://mempool.space/tx/push")


if __name__ == "__main__":
    main()
