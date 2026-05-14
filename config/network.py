import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (parent of this config/ directory)
load_dotenv(Path(__file__).parent.parent / ".env")

_STATIC = {
    "testnet": {
        "network_name":    "0G Galileo Testnet",
        "chain_id":        16602,
        "rpc_url":         "https://evmrpc-testnet.0g.ai",
        "storage_indexer": "https://indexer-storage-testnet-turbo.0g.ai",
        "explorer":        "https://chainscan-galileo.0g.ai",
        "faucet_msg":      "Get tokens at https://faucet.0g.ai",
    },
    "mainnet": {
        "network_name":    "0G Aristotle Mainnet",
        "chain_id":        16661,
        "rpc_url":         "https://evmrpc.0g.ai",
        "storage_indexer": "https://indexer-storage-turbo.0g.ai",
        "explorer":        "https://chainscan.0g.ai",
        "faucet_msg":      "Buy OG on KuCoin / Gate.io / MEXC / LBank and withdraw to your 0G Mainnet wallet (Chain ID 16661)",
    },
}


def get_network_config() -> dict:
    net = os.getenv("NETWORK", "testnet").lower().strip()
    if net not in _STATIC:
        raise ValueError(f"Unknown NETWORK={net!r}. Choose 'testnet' or 'mainnet'.")
    cfg = dict(_STATIC[net])
    if net == "testnet":
        cfg["agent_executor"] = os.getenv("AGENT_EXECUTOR_ADDRESS", "0xAD561D06c61a11f577b9E89FdEF88AC2eE826ba6")
        cfg["aip_v2"]         = os.getenv("AIP_ADDRESS_V2",          "0xB6B4C9cF47D36b8D6B425B4473F9CE81256BE2c7")
    else:
        # Mainnet uses distinct env var names so testnet/mainnet can coexist in the same .env
        cfg["agent_executor"] = os.getenv("AGENT_EXECUTOR_MAINNET", os.getenv("AGENT_EXECUTOR_ADDRESS", ""))
        cfg["aip_v2"]         = os.getenv("AIP_CONTRACT_MAINNET",   os.getenv("AIP_ADDRESS_V2", ""))
    return cfg
