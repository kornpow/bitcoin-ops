# Bitcoin OP_RETURN Transaction Creator

A Python tool for creating and signing Bitcoin transactions with OP_RETURN outputs on testnet (or mainnet). This project uses the `embit` library for Bitcoin operations and is managed with the `uv` package manager.

## Features

- 🔑 **Automatic Key Management**: Generates and securely stores a Bitcoin private key on first run
- 💰 **Automatic UTXO Discovery**: Fetches available UTXOs from your wallet using mempool.space API
- 📝 **Custom OP_RETURN Data**: Embed any text data into the Bitcoin blockchain
- ⚙️ **Configurable Fee Rates**: Set custom fee rates in sat/vB
- 🔒 **Segwit Support**: Uses P2WPKH (native segwit) addresses for lower fees
- 💸 **Dust Limit Handling**: Automatically handles change outputs below dust limit
- 🧪 **Testnet & Mainnet Support**: Safe testing on testnet before using mainnet

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

### 3. Create an OP_RETURN Transaction

Once you have funds, create a transaction with custom data:

```bash
uv run main.py --data "Hello Bitcoin!"
```

This will:
- Fetch your available UTXOs
- Create a transaction with an OP_RETURN output containing your data
- Add a change output to return remaining funds to your wallet
- Sign the transaction
- Display the transaction hex for broadcasting

### 4. Broadcast the Transaction

Copy the transaction hex and paste it at:
- Testnet: <https://mempool.space/testnet/tx/push>
- Mainnet: <https://mempool.space/tx/push>

## Usage Examples

### Check wallet balance
```bash
uv run main.py --check-balance
```

### Create OP_RETURN with custom data
```bash
uv run main.py --data "Hello Bitcoin!"
```

### Automatically broadcast to mempool.space
```bash
uv run main.py --data "GM Bitcoin!" --broadcast
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
uv run main.py --wallet-file my-wallet.key --data "Hello!"
```

### Use mainnet (⚠️ BE CAREFUL!)
```bash
uv run main.py --network main --data "Mainnet data" --fee-rate 5
```

## Command-Line Options

```
Options:
  --wallet-file PATH           Path to wallet key file (default: wallet.key)
  --network {test,main}        Bitcoin network (default: test)
  --data TEXT                  Data to include in OP_RETURN output
  --fee-rate INT               Fee rate in sat/vB (default: 2)
  --check-balance              Check wallet balance and available UTXOs
  --utxo-index INT             Index of UTXO to use (if multiple available)
  --allow-large-opreturn       Allow OP_RETURN data >80 bytes (may not relay)
  --broadcast                  Automatically broadcast to mempool.space
  -h, --help                   Show help message
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
