# Bitcoin OP_RETURN Transaction Creator

A Python tool for creating and signing Bitcoin transactions with OP_RETURN outputs on testnet (or mainnet). This project uses the `embit` library for Bitcoin operations and is managed with the `uv` package manager.

## Features

- 🔑 **Automatic Key Management**: Generates and (in)-securely stores a Bitcoin private key on first run
- 💰 **Automatic UTXO Discovery**: Fetches available UTXOs from your wallet using mempool.space API
- 📜 **Transaction History**: View all historical OP_RETURN transactions from your wallet
- 📝 **Custom OP_RETURN Data**: Embed any text data into the Bitcoin blockchain
- ⚙️ **Configurable Fee Rates**: Set custom fee rates in sat/vB
- 🔒 **Segwit Support**: Uses P2WPKH (native segwit) addresses for lower fees
- 💸 **Dust Limit Handling**: Automatically handles change outputs below dust limit
- 🧪 **Testnet & Mainnet Support**: Safe testing on testnet before using mainnet
- 📡 **Multiple Broadcast Methods**: mempool.space API, local Bitcoin Core RPC, or manual
- 🔧 **Large OP_RETURN Support**: Bypass 80-byte limit with local node configuration

## Requirements

- Python 3.13+
- `uv` package manager
- Dependencies: `embit`, `requests`

## Installation

1. **Clone the repository** (or download the files):
   ```bash
   git clone <your-repo-url>
   cd bitcoin-ops
   ```

2. **Install dependencies with uv**:
   ```bash
   uv sync
   ```

## Quick Start

### 1. Generate Wallet and Check Balance

On first run, the script will automatically generate a new Bitcoin private key and save it to `wallet.key`:

```bash
uv run main.py --check-balance
```

This will:
- Generate a new private key (saved to `wallet.key` with restricted permissions)
- Display your testnet address
- Check for available UTXOs and show your balance

**⚠️ IMPORTANT**: The `wallet.key` file contains your private key. Keep it secure and never share it!

### 2. Get Testnet Coins

Visit a Bitcoin testnet faucet and send some coins to your address:
- <https://testnet-faucet.mempool.co/>
- <https://coinfaucet.eu/en/btc-testnet/>
- <https://bitcoinfaucet.uo1.net/>

### 3. Create an OP_RETURN Transaction

Once you have funds, create a transaction with custom data:

```bash
uv run main.py --data "Remember Me, Bitcoin Ops!"
```

This will:
- Fetch your available UTXOs
- Create a transaction with an OP_RETURN output containing your data
- Add a change output to return remaining funds to your wallet
- Sign the transaction
- Display the transaction hex for broadcasting

### 4. Broadcast the Transaction

**Option A: Using a Web Service**

Copy the transaction hex and paste it at:
- Testnet: <https://mempool.space/testnet/tx/push>
- Mainnet: <https://mempool.space/tx/push>

**Option B: Using a Local Bitcoin Node**

If you're running a local Bitcoin node, broadcast directly using `bitcoin-cli`:
```bash
bitcoin-cli sendrawtransaction <transaction_hex>
```

For testnet:
```bash
bitcoin-cli -testnet sendrawtransaction <transaction_hex>
```

## Usage Examples

### Check wallet balance
```bash
uv run main.py --check-balance
```

### View historical OP_RETURN transactions
```bash
uv run main.py --history
```

### Create OP_RETURN with custom data
```bash
uv run main.py --data "Hello Bitcoin!"
```

### Automatically broadcast to mempool.space
```bash
uv run main.py --data "GM Bitcoin!" --broadcast
```

### Broadcast to local Bitcoin Core node
```bash
# Using separate credentials
uv run main.py --data "My message" --rpc-user myuser --rpc-password mypass

# Using full RPC URL
uv run main.py --data "My message" --rpc-url http://user:pass@localhost:18332

# Custom host and port
uv run main.py --data "My message" --rpc-user myuser --rpc-password mypass --rpc-host 192.168.1.100 --rpc-port 18332
```

### Use a specific UTXO (if you have multiple)
```bash
uv run main.py --data "My message" --utxo-index 0
```

### Set custom fee rate (sat/vB)
```bash
uv run main.py --data "Important data" --fee-rate 3
```

### Use custom wallet file
```bash
# Specify a custom wallet file location
uv run main.py --wallet-file my-wallet.key --data "Hello!"

# Use a wallet in a different directory (supports ~ expansion)
uv run main.py --wallet-file ~/.bitcoin-wallets/testnet.key --check-balance

# Use environment variable for wallet location
export BITCOIN_OPS_WALLET=~/wallets/testnet.key
uv run main.py --check-balance
```

**Note**: The wallet file path supports:
- Relative paths (e.g., `wallet.key`, `wallets/my-wallet.key`)
- Absolute paths (e.g., `/home/user/wallets/my-wallet.key`)
- Tilde expansion (e.g., `~/my-wallet.key`)
- Environment variable override via `BITCOIN_OPS_WALLET`
- Parent directories are automatically created with secure permissions (0700)

### Use mainnet (⚠️ BE CAREFUL!)
```bash
uv run main.py --network main --data "Mainnet data" --fee-rate 5
```

## Command-Line Options

```
Options:
  --wallet-file PATH           Path to wallet key file (default: wallet.key, supports ~ expansion)
  --network {test,main}        Bitcoin network (default: test)
  --data TEXT                  Data to include in OP_RETURN output
  --fee-rate FLOAT             Fee rate in sat/vB (default: 2.0, supports fractional rates)
  --check-balance              Check wallet balance and available UTXOs
  --history                    Show all historical OP_RETURN transactions
  --utxo-index INT             Index of UTXO to use (if multiple available)
  --allow-large-opreturn       Allow OP_RETURN data >80 bytes (may not relay)
  --broadcast                  Automatically broadcast to mempool.space
  --rpc-url RPC_URL            Bitcoin Core RPC URL (http://user:pass@host:port)
  --rpc-user RPC_USER          Bitcoin Core RPC username
  --rpc-password RPC_PASSWORD  Bitcoin Core RPC password
  --rpc-host RPC_HOST          Bitcoin Core RPC host (default: localhost)
  --rpc-port RPC_PORT          Bitcoin Core RPC port (18332 testnet, 8332 mainnet)
  --rpc-only                   Use ONLY RPC (scantxoutset, no external API)
  -h, --help                   Show help message
```

**Environment Variables:**
- `BITCOIN_OPS_WALLET`: Path to wallet file (overrides `--wallet-file`)

## Broadcasting Options

The tool supports three ways to broadcast your transaction:

### 1. mempool.space API (easiest)
```bash
uv run main.py --data "My message" --broadcast
```
- No setup required
- Works immediately
- Limited to 80-byte OP_RETURN for standard relay

### 2. Local Bitcoin Core RPC
```bash
uv run main.py --data "My message" --rpc-user myuser --rpc-password mypass
```
- Requires Bitcoin Core running locally
- Can handle larger OP_RETURN with custom `-datacarriersize`
- More privacy (doesn't expose transaction to third party)
- Full control over relay policy

### 3. Manual broadcast
Copy the transaction hex from the output and paste it at:
- Testnet: https://mempool.space/testnet/tx/push
- Mainnet: https://mempool.space/tx/push

**Tip:** Use the `--broadcast` flag to automatically broadcast to mempool.space API instead of manual copy/paste.

### Bitcoin Core RPC Setup

Bitcoin Core supports two authentication methods for RPC:

#### Option 1: Cookie Authentication (Default - Easiest)

If you're running Bitcoin Core with default settings, it uses cookie-based authentication. The cookie file contains auto-generated credentials.

**For Testnet:**
```bash
# Extract the cookie password once (Linux/macOS)
COOKIE_PASS=$(cut -d: -f2 ~/.bitcoin/testnet3/.cookie)

# Or on macOS with alternative path
COOKIE_PASS=$(cut -d: -f2 ~/Library/Application\ Support/Bitcoin/testnet3/.cookie)

# Now use it for your transactions
uv run main.py --data "Your message" --rpc-user "__cookie__" --rpc-password "$COOKIE_PASS"
```

**Cookie file locations:**
- **Linux**: `~/.bitcoin/testnet3/.cookie` (testnet) or `~/.bitcoin/.cookie` (mainnet)
- **macOS**: `~/Library/Application Support/Bitcoin/testnet3/.cookie` (testnet) or `~/Library/Application Support/Bitcoin/.cookie` (mainnet)
- **Windows**: `%APPDATA%\Bitcoin\testnet3\.cookie` (testnet) or `%APPDATA%\Bitcoin\.cookie` (mainnet)

The cookie file format is `__cookie__:long_random_hex_string`. The username is always `__cookie__` and the password is the part after the colon.

**Note**: The cookie file is only present while Bitcoin Core is running and changes on each restart.

#### Option 2: Username/Password Authentication

For a persistent setup, add credentials to your `bitcoin.conf`:

```conf
server=1
rpcuser=yourusername
rpcpassword=yourpassword
rpcallowip=127.0.0.1

# Optional: increase OP_RETURN limit (default is 80)
datacarriersize=1000
```

**Config file locations:**
- **Linux**: `~/.bitcoin/bitcoin.conf`
- **macOS**: `~/Library/Application Support/Bitcoin/bitcoin.conf`
- **Windows**: `%APPDATA%\Bitcoin\bitcoin.conf`

Then restart Bitcoin Core and use:
```bash
uv run main.py --data "Your data" --rpc-user yourusername --rpc-password yourpassword
```

### Bitcoin Core Mempool Policy Configuration

These configuration options control how your Bitcoin Core node handles OP_RETURN transactions and mempool relay policies:

#### Non-Standard Transaction Policy Options

```conf
# Enable or disable relaying of OP_RETURN transactions (default: 1)
datacarrier=1

# Maximum size of OP_RETURN data in bytes (default: 80)
# Increase this to relay/accept larger OP_RETURN transactions
datacarriersize=1000

# Allow relaying bare multisig outputs (default: 1)
# Bare multisig = multisig not wrapped in P2SH/P2WSH
# This is necessary to have multiple op-returns in a single txn
permitbaremultisig=1

# Allow relaying non-standard transactions (default: 0 on mainnet, 1 on testnet)
# Includes large OP_RETURN, unusual script types, etc.
# Only affects relay, not mining policy
acceptnonstdtxn=1
```

**Key Points:**
- **Standard relay limit**: By default, most testnet nodes relay OP_RETURN up to 80 bytes
- **Local node benefits**: With a local node, you can increase `datacarriersize` to handle larger data
- **Network propagation**: Transactions >80 bytes may not propagate well across the network
- **Mining**: Even with custom settings, miners still control what gets into blocks

#### General Mempool Options

```conf
# Maximum mempool size in MB (default: 300)
maxmempool=500

# Minimum relay transaction fee in BTC/kB (default: 0.00001)
minrelaytxfee=0.00001

# Enable full Replace-By-Fee (default: 0)
mempoolfullrbf=1

# Reject transactions with high fees (spam protection, default: 0)
blockmaxweight=3996000
```

#### Complete Example Configuration

Here's a recommended `bitcoin.conf` for OP_RETURN development:

```conf
# Basic RPC setup
server=1
rpcuser=yourusername
rpcpassword=yourpassword
rpcallowip=127.0.0.1

# Non-standard transaction policy
datacarrier=1
datacarriersize=1000
permitbaremultisig=1
acceptnonstdtxn=1

# Mempool settings
maxmempool=500
minrelaytxfee=0.00001

# For testnet
testnet=1
```

**After changing `bitcoin.conf`:**
1. Save the file
2. Restart Bitcoin Core completely
3. Verify settings: `bitcoin-cli getmempoolinfo`

#### Runtime Commands

You can also check current mempool policy at runtime:

```bash
# Check mempool information
bitcoin-cli getmempoolinfo

# Check network info (includes relay fees)
bitcoin-cli getnetworkinfo

# Test if a transaction would be accepted (without actually broadcasting)
bitcoin-cli testmempoolaccept '["<hex_transaction>"]'
```

## How It Works

### Key Generation and Storage

1. **First Run**: When you run the script for the first time, it:
   - Generates a secure 256-bit random private key using `os.urandom(32)`
   - Converts the key to WIF (Wallet Import Format) for storage
   - Saves the WIF to `wallet.key` with restricted file permissions (0600)
   - Derives the public key and P2WPKH address

2. **Subsequent Runs**: The script loads the existing private key from `wallet.key`

### Transaction Creation

1. **UTXO Discovery**: Fetches available UTXOs from mempool.space API
2. **Transaction Building**:
   - Creates a transaction with one input (selected UTXO)
   - Adds OP_RETURN output with your custom data (value = 0)
   - Calculates fee based on estimated transaction size and fee rate
   - Adds change output if remaining amount is above dust limit (546 sats)
3. **Signing**: Uses PSBT (Partially Signed Bitcoin Transaction) to sign the transaction
4. **Output**: Provides the final transaction hex for broadcasting

### Fee Calculation

The script estimates transaction size based on:
- Base overhead: ~10 bytes
- P2WPKH input: ~68 vbytes
- OP_RETURN output: ~10 + data length bytes
- Change output: ~31 vbytes (P2WPKH)

Fee = estimated_vsize × fee_rate (sat/vB)

## File Structure

```
bitcoin-ops/
├── main.py           # Main script with all functionality
├── wallet.key        # Your private key (generated on first run, DO NOT COMMIT!)
├── pyproject.toml    # UV project configuration
├── uv.lock           # Locked dependencies
└── README.md         # This file
```

## Security Notes

⚠️ **IMPORTANT SECURITY CONSIDERATIONS**:

1. **Private Key Storage**: The `wallet.key` file contains your private key in WIF format. Anyone with access to this file can spend your funds.

2. **File Permissions**: The script automatically sets restrictive permissions (0600) on `wallet.key` to prevent unauthorized access.

3. **Version Control**: Never commit `wallet.key` to git! The `.gitignore` file should include:
   ```
   wallet.key
   *.key
   ```

4. **Testnet vs Mainnet**: Always test on testnet first. Mainnet transactions involve real money!

5. **Backup**: Keep a secure backup of your `wallet.key` file if it contains funds you care about.

## Troubleshooting

### "No UTXOs found"
- Make sure you've sent testnet coins to your address
- Wait for at least one confirmation
- Check the address on a block explorer: `https://mempool.space/testnet/address/YOUR_ADDRESS`

### "Error fetching UTXOs"
- Check your internet connection
- The mempool.space API might be temporarily unavailable
- Try again in a few moments

### "Change amount is below dust limit"
- Your UTXO is too small to cover the fee and leave change
- Get more testnet coins or use a larger UTXO
- The entire UTXO will be used as fee (except the OP_RETURN output)

### "OP_RETURN data is >80 bytes"
- While Bitcoin allows larger OP_RETURN data, the standard limit is 80 bytes
- Some nodes might not relay transactions with larger OP_RETURN outputs
- Consider using shorter data or splitting across multiple transactions

## Technical Details

### Libraries Used

- **embit**: Bitcoin library for Python providing transaction building, signing, and key management
- **requests**: HTTP library for API calls to mempool.space

### Address Type

The script uses **P2WPKH (Pay to Witness Public Key Hash)**, also known as native segwit:
- Addresses start with `tb1` (testnet) or `bc1` (mainnet)
- Lower transaction fees compared to legacy addresses
- Better privacy and efficiency

### Transaction Structure

Example transaction structure:
```
Version: 2
Inputs: 1
  - Previous TXID: <utxo_txid>
  - Output Index: <vout>
  - Witness: <signature> <pubkey>
Outputs: 2
  - OP_RETURN: <your_data> (value: 0)
  - Change: <your_address> (value: input - fee)
Locktime: 0
```

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

This project is open source. See LICENSE file for details.

## Disclaimer

This software is provided "as is" without warranty of any kind. Use at your own risk. The authors are not responsible for any loss of funds. Always test with small amounts first, especially on mainnet.
