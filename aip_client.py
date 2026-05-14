"""
aip_client.py -- Python wrapper for AIPSensoryLayer + AgentExecutor.

Supports 0G Galileo Testnet (chain 16602) and 0G Aristotle Mainnet (chain 16661).
Select network via NETWORK env var: "testnet" (default) | "mainnet"
Contract addresses are read from AGENT_EXECUTOR_ADDRESS / AIP_ADDRESS_V2 env vars
(testnet defaults are baked into config/network.py).

Primary usage (P7+):
    from aip_client import AIPClient
    client = AIPClient()
    result = client.execute_with_aip_check(intent_data=b"my_intent")

Architecture:
    EOA  -->  AgentExecutor.execute(intentData)
                  |
                  +--> AIPSensoryLayer.preCheck(...)   [TSTORE written]
                  +--> emit ExecutionTriggered(...)     [mock action]
                  +--> AIPSensoryLayer.postCheck(...)   [TSTORE read & cleared]

    All three steps happen in ONE atomic transaction, satisfying EIP-1153.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from eth_abi import encode as abi_encode
from web3 import Web3
from config.network import get_network_config

load_dotenv(Path(__file__).parent / ".env")

# ── Network config ─────────────────────────────────────────────────────────────
_NET                   = get_network_config()
AGENT_EXECUTOR_ADDRESS = _NET["agent_executor"]
AIP_ADDRESS_V2         = _NET["aip_v2"]
AIP_ADDRESS_V1         = "0xEf74C4317e92B148f9F3E8DAd0A4891C25Ad69D0"  # testnet-only, deprecated

RPC_URL     = _NET["rpc_url"]
CHAIN_ID    = _NET["chain_id"]
MIN_GAS_TIP = Web3.to_wei(3, "gwei")

_ABI_DIR = Path(__file__).parent / "abi"


# ── Exception ──────────────────────────────────────────────────────────────────
class AIPError(Exception):
    """Raised for any AIP / AgentExecutor interaction failure."""


# ── Client ─────────────────────────────────────────────────────────────────────
class AIPClient:
    def __init__(
        self,
        executor_address: str = AGENT_EXECUTOR_ADDRESS,
        aip_address: str      = AIP_ADDRESS_V2,
        executor_abi: str     = str(_ABI_DIR / "AgentExecutor.json"),
        aip_abi: str          = str(_ABI_DIR / "AIPSensoryLayer.json"),
    ):
        rpc = os.environ.get("ZEROG_RPC_URL", RPC_URL)
        self.w3 = Web3(Web3.HTTPProvider(rpc))
        if not self.w3.is_connected():
            raise AIPError(f"Cannot connect to RPC: {rpc}")

        network = os.environ.get("NETWORK", "testnet").strip().lower()
        key_var = "ZEROG_MAINNET_PRIVATE_KEY" if network == "mainnet" else "ZEROG_PRIVATE_KEY"
        raw_key = os.environ.get(key_var, "").strip()
        if not raw_key:
            raise AIPError(f"{key_var} not set in .env or environment (NETWORK={network})")

        from eth_account import Account
        self.signer = Account.from_key(raw_key)

        # AgentExecutor contract
        with open(executor_abi) as f:
            exec_abi = json.load(f)
        self.executor = self.w3.eth.contract(
            address=Web3.to_checksum_address(executor_address),
            abi=exec_abi,
        )

        # AIPSensoryLayer contract (read-only queries, event decoding)
        with open(aip_abi) as f:
            aip_abi_data = json.load(f)
        self.aip = self.w3.eth.contract(
            address=Web3.to_checksum_address(aip_address),
            abi=aip_abi_data,
        )

        # Build selector -> error name map for revert decoding (from AIP ABI)
        self._errors: dict = {}
        for item in aip_abi_data:
            if item["type"] == "error":
                types = ",".join(inp["type"] for inp in item["inputs"])
                sig   = f"{item['name']}({types})"
                sel   = self.w3.keccak(text=sig)[:4].hex()
                self._errors[sel] = item

    # ── Primary API (P7+) ──────────────────────────────────────────────────────

    def execute_with_aip_check(self, intent_data: bytes) -> dict:
        """
        Call AgentExecutor.execute(intent_data) on-chain.

        Atomically executes preCheck -> ExecutionTriggered -> postCheck in one tx,
        satisfying EIP-1153 transient storage integrity across the full intent lifecycle.

        Returns:
            {
                "tx_hash":      "0x...",
                "block_number": int,
                "gas_used":     int,
                "logs":         [{"event": str, "args": dict}, ...]
            }
        """
        fn = self.executor.functions.execute(intent_data)
        result = self._build_and_send(fn, "execute")

        receipt = result["receipt"]
        logs    = self._decode_logs(receipt)

        return {
            "tx_hash":      result["tx_hash"],
            "block_number": receipt["blockNumber"],
            "gas_used":     receipt["gasUsed"],
            "logs":         logs,
        }

    def compute_intent_hash(self, agent_output: str, run_id: str) -> bytes:
        """keccak256(run_id_bytes || agent_output_bytes) -- Harness-level commitment."""
        return bytes(self.w3.keccak(run_id.encode() + agent_output.encode()))

    # ── Legacy API (P6, deprecated) ────────────────────────────────────────────

    def pre_check(self, pools=None, tokens=None, spenders=None, min_amounts_out=None):
        """
        DEPRECATED: Direct preCheck call (separate tx, EIP-1153 incompatible with postCheck).
        Use execute_with_aip_check() for the full atomic intent lifecycle.
        Kept for reference and backward compatibility with P6 tests.
        """
        pools           = pools           or []
        tokens          = tokens          or []
        spenders        = spenders        or []
        min_amounts_out = min_amounts_out or []

        msg_data = self._encode_msg_data(pools, tokens, spenders, min_amounts_out)
        fn = self.aip.functions.preCheck(self.signer.address, 0, msg_data)

        try:
            hook_data = fn.call({"from": self.signer.address})
        except Exception as e:
            raise AIPError(f"preCheck simulation failed: {e}") from e

        result = self._build_and_send(fn, "preCheck")
        return result["tx_hash"], hook_data

    def post_check(self, hook_data: bytes) -> str:
        """
        DEPRECATED: Direct postCheck call. Will revert with AIP__NoActiveIntent
        because EIP-1153 transient storage is cleared between separate transactions.
        Use execute_with_aip_check() instead.
        """
        fn = self.aip.functions.postCheck(hook_data)
        result = self._build_and_send(fn, "postCheck")
        return result["tx_hash"]

    # ── Internal ───────────────────────────────────────────────────────────────

    def _encode_msg_data(self, pools, tokens, spenders, min_amounts_out) -> bytes:
        DUMMY_SELECTOR = b"\x00\x00\x00\x00"
        if not pools and not tokens and not spenders:
            return DUMMY_SELECTOR
        payload = abi_encode(
            ["address[]", "address[]", "address[]", "uint256[]"],
            [
                [Web3.to_checksum_address(a) for a in pools],
                [Web3.to_checksum_address(a) for a in tokens],
                [Web3.to_checksum_address(a) for a in spenders],
                list(min_amounts_out),
            ],
        )
        return DUMMY_SELECTOR + payload

    def _build_and_send(self, fn_call, label: str) -> dict:
        """Build EIP-1559 tx with 3 gwei tip, sign, broadcast, wait for receipt."""
        nonce    = self.w3.eth.get_transaction_count(self.signer.address, "pending")
        block    = self.w3.eth.get_block("latest")
        base_fee = block.get("baseFeePerGas") or Web3.to_wei(1, "gwei")

        try:
            tx = fn_call.build_transaction({
                "from":                 self.signer.address,
                "nonce":                nonce,
                "maxPriorityFeePerGas": MIN_GAS_TIP,
                "maxFeePerGas":         base_fee * 2 + MIN_GAS_TIP,
                "chainId":              CHAIN_ID,
            })
        except Exception as e:
            if "insufficient funds" in str(e).lower():
                raise AIPError(
                    f"Insufficient funds ({self.signer.address}). "
                    + _NET["faucet_msg"]
                ) from None
            self._raise_if_known_revert(str(e), label)
            raise AIPError(f"{label} build_transaction failed: {e}") from e

        signed = self.signer.sign_transaction(tx)
        raw_tx = getattr(signed, "raw_transaction", None) or signed.rawTransaction

        try:
            tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
        except Exception as e:
            msg = str(e).lower()
            if "insufficient funds" in msg:
                raise AIPError(
                    f"Insufficient funds ({self.signer.address}). "
                    + _NET["faucet_msg"]
                ) from None
            if "timeout" in msg or "connection" in msg:
                raise AIPError(
                    f"Network error ({RPC_URL}). Check connectivity and retry."
                ) from None
            raise AIPError(f"{label} send failed: {e}") from e

        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        except Exception:
            raise AIPError(
                f"{label} receipt timeout (tx: 0x{tx_hash.hex()}). "
                f"Check {_NET['explorer']}"
            ) from None

        if receipt["status"] == 0:
            self._decode_and_raise(tx_hash, label)

        return {"tx_hash": "0x" + tx_hash.hex(), "receipt": receipt}

    def _decode_logs(self, receipt) -> list:
        """Decode known events from both AgentExecutor and AIPSensoryLayer."""
        events = []
        for contract in (self.executor, self.aip):
            for log in receipt["logs"]:
                for event in contract.events:
                    try:
                        decoded = event().process_log(log)
                        events.append({
                            "event": decoded["event"],
                            "args":  dict(decoded["args"]),
                        })
                    except Exception:
                        pass
        return events

    def _raise_if_known_revert(self, raw: str, label: str):
        for token in raw.replace("'", "").replace('"', "").split():
            token = token.rstrip(",)(")
            if token.startswith("0x") and len(token) >= 10:
                sel = token[2:10]
                if sel in self._errors:
                    raise AIPError(
                        f"{label} reverted: {self._errors[sel]['name']}"
                    ) from None

    def _decode_and_raise(self, tx_hash: bytes, label: str):
        tx      = self.w3.eth.get_transaction(tx_hash)
        err_str = "unknown revert"
        try:
            self.w3.eth.call(
                {"from": tx["from"], "to": tx["to"], "data": tx["input"]},
                tx["blockNumber"],
            )
        except Exception as exc:
            raw = str(exc)
            self._raise_if_known_revert(raw, label)
            err_str = raw[:200]
        raise AIPError(f"{label} reverted: {err_str}")
