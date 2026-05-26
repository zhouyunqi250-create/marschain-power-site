#!/usr/bin/env python3
"""Payment verification API for MarsChain paid leaderboard downloads."""

from __future__ import annotations

import json
import os
import time
import uuid
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


RPC_URL = os.getenv("MARSCHAIN_RPC_URL", "https://rpcs.marschain.net")
PAY_TO_DISPLAY = os.getenv("MARS_PAYMENT_ADDRESS_DISPLAY", "0M8678F454D69d2185DfAa6643cF06faCB8DE17c7c")
PAY_TO_VERIFY = os.getenv("MARS_PAYMENT_ADDRESS_VERIFY", "0x8678F454D69d2185DfAa6643cF06faCB8DE17c7c")
PRICE_MARS = os.getenv("MARS_PAID_DOWNLOAD_PRICE", "1000")
PRICE_WEI = int(os.getenv("MARS_PAID_DOWNLOAD_PRICE_WEI", "1000000000000000000000"))
DOWNLOAD_EXPIRES_SECONDS = int(os.getenv("MARS_PAID_DOWNLOAD_EXPIRES_SECONDS", "3600"))
REQUIRED_CONFIRMATIONS = int(os.getenv("MARS_PAYMENT_CONFIRMATIONS", "3"))
ROUTE_PREFIX = os.getenv("MARS_PAID_ROUTE_PREFIX", "/api").rstrip("/")
ORDER_PREFIX = os.getenv("MARS_PAID_ORDER_PREFIX", "paid-download/orders").strip("/")
PAID_DOWNLOAD_PREFIX = os.getenv("ALIYUN_PAID_OSS_PREFIX", "paid-downloads").strip("/")
ALLOWED_FORMATS = {
    "csv": os.getenv("MARS_PAID_DOWNLOAD_CSV_OBJECT", f"{PAID_DOWNLOAD_PREFIX}/latest.csv").strip("/"),
    "xlsx": os.getenv("MARS_PAID_DOWNLOAD_XLSX_OBJECT", f"{PAID_DOWNLOAD_PREFIX}/latest.xlsx").strip("/"),
}
CORS_ORIGIN = os.getenv("MARS_PAID_CORS_ORIGIN", "*")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


@dataclass
class Request:
    method: str
    path: str
    body: dict[str, Any]


def normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return f"https://{endpoint}"


def normalize_address(address: str) -> str:
    value = address.strip()
    if value.startswith("0M") and len(value) == 42:
        return "0x" + value[2:]
    return value


def hex_to_int(value: str | None) -> int:
    if not value:
        return 0
    return int(value, 16)


def now_ts() -> int:
    return int(time.time())


def rpc_call(method: str, params: list[Any]) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(
        RPC_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read())
    if data.get("error"):
        raise ApiError(502, "RPC_ERROR", str(data["error"]))
    return data.get("result")


def latest_block_number() -> int:
    return hex_to_int(rpc_call("eth_blockNumber", []))


def get_bucket():
    try:
        import oss2
    except ModuleNotFoundError as exc:
        raise ApiError(500, "DEPENDENCY_MISSING", "服务缺少 oss2 依赖，请先安装 requirements.txt") from exc

    access_key_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
    access_key_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    bucket_name = os.getenv("ALIYUN_PAID_OSS_BUCKET")
    endpoint = os.getenv("ALIYUN_PAID_OSS_ENDPOINT") or os.getenv("ALIYUN_OSS_ENDPOINT")
    if not access_key_id or not access_key_secret or not bucket_name or not endpoint:
        raise ApiError(500, "CONFIG_MISSING", "付费下载服务还没有完成 OSS 配置")
    auth = oss2.Auth(access_key_id, access_key_secret)
    return oss2.Bucket(auth, normalize_endpoint(endpoint), bucket_name)


def order_key(order_id: str) -> str:
    return f"{ORDER_PREFIX}/{order_id}.json"


def tx_key(tx_hash: str) -> str:
    return f"{ORDER_PREFIX}/tx/{tx_hash.lower()}.json"


def read_json_object(bucket: Any, key: str) -> dict[str, Any]:
    try:
        return json.loads(bucket.get_object(key).read())
    except Exception as exc:
        if exc.__class__.__name__ in {"NoSuchKey", "NoSuchObject"}:
            raise ApiError(404, "NOT_FOUND", "订单不存在")
        raise


def write_json_object(bucket: Any, key: str, payload: dict[str, Any]) -> None:
    bucket.put_object(
        key,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"},
    )


def parse_request(environ: dict[str, Any]) -> Request:
    method = (environ.get("REQUEST_METHOD") or "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    if ROUTE_PREFIX and path.startswith(ROUTE_PREFIX + "/"):
        path = path[len(ROUTE_PREFIX) :]
    elif ROUTE_PREFIX and path == ROUTE_PREFIX:
        path = "/"
    length = int(environ.get("CONTENT_LENGTH") or 0)
    raw_body = environ["wsgi.input"].read(length) if length else b""
    body: dict[str, Any] = {}
    if raw_body:
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            raise ApiError(400, "BAD_JSON", "请求内容不是有效 JSON")
    return Request(method=method, path=path, body=body)


def pick_format(value: Any) -> str:
    fmt = str(value or "xlsx").lower()
    if fmt not in ALLOWED_FORMATS:
        raise ApiError(400, "BAD_FORMAT", "下载格式不支持")
    return fmt


def sign_download_url(bucket: Any, fmt: str, expires_at: int) -> str:
    ttl = max(1, min(DOWNLOAD_EXPIRES_SECONDS, expires_at - now_ts()))
    return bucket.sign_url("GET", ALLOWED_FORMATS[fmt], ttl)


def create_order(body: dict[str, Any]) -> dict[str, Any]:
    fmt = pick_format(body.get("format"))
    bucket = get_bucket()
    created_at = now_ts()
    order_id = uuid.uuid4().hex
    order = {
        "id": order_id,
        "status": "pending",
        "format": fmt,
        "amountMars": PRICE_MARS,
        "amountWei": str(PRICE_WEI),
        "payTo": PAY_TO_DISPLAY,
        "payToVerify": normalize_address(PAY_TO_VERIFY),
        "createdAt": created_at,
        "createdBlockNumber": latest_block_number(),
        "downloadExpiresSeconds": DOWNLOAD_EXPIRES_SECONDS,
    }
    write_json_object(bucket, order_key(order_id), order)
    return {
        "orderId": order_id,
        "status": order["status"],
        "format": fmt,
        "amountMars": PRICE_MARS,
        "payTo": PAY_TO_DISPLAY,
        "downloadExpiresSeconds": DOWNLOAD_EXPIRES_SECONDS,
    }


def load_order(bucket: Any, order_id: str) -> dict[str, Any]:
    if not order_id or not order_id.replace("-", "").isalnum():
        raise ApiError(400, "BAD_ORDER", "订单号格式不正确")
    return read_json_object(bucket, order_key(order_id))


def ensure_tx_unused(bucket: Any, tx_hash: str, order_id: str) -> None:
    try:
        used = read_json_object(bucket, tx_key(tx_hash))
    except ApiError as exc:
        if exc.status == 404:
            return
        raise
    if used.get("orderId") != order_id:
        raise ApiError(409, "TX_REUSED", "这笔交易已经核销过")


def verify_transaction(order: dict[str, Any], tx_hash: str) -> dict[str, Any]:
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        raise ApiError(400, "BAD_TX_HASH", "交易哈希格式不正确")

    tx = rpc_call("eth_getTransactionByHash", [tx_hash])
    if not tx:
        raise ApiError(404, "TX_NOT_FOUND", "链上还没有查到这笔交易")

    receipt = rpc_call("eth_getTransactionReceipt", [tx_hash])
    if not receipt:
        raise ApiError(409, "TX_PENDING", "交易还未确认")

    if str(receipt.get("status", "")).lower() != "0x1":
        raise ApiError(400, "TX_FAILED", "这笔交易链上状态不是成功")

    expected_to = normalize_address(order["payToVerify"]).lower()
    actual_to = normalize_address(str(tx.get("to") or "")).lower()
    if actual_to != expected_to:
        raise ApiError(400, "WRONG_RECEIVER", "收款地址不匹配")

    value = hex_to_int(tx.get("value"))
    if value < int(order["amountWei"]):
        raise ApiError(400, "AMOUNT_TOO_LOW", "转账金额不足 1000 MARS")

    tx_block = hex_to_int(tx.get("blockNumber") or receipt.get("blockNumber"))
    if tx_block < int(order.get("createdBlockNumber") or 0):
        raise ApiError(400, "OLD_TX", "这笔交易早于订单创建时间")

    latest_block = latest_block_number()
    confirmations = max(0, latest_block - tx_block + 1)
    if confirmations < REQUIRED_CONFIRMATIONS:
        raise ApiError(
            409,
            "WAITING_CONFIRMATIONS",
            "交易确认数还不够",
            {"confirmations": confirmations, "requiredConfirmations": REQUIRED_CONFIRMATIONS},
        )

    return {"txBlockNumber": tx_block, "confirmations": confirmations, "valueWei": str(value)}


def verify_order(order_id: str, body: dict[str, Any]) -> dict[str, Any]:
    fmt = pick_format(body.get("format"))
    tx_hash = str(body.get("txHash") or body.get("tx_hash") or "").strip().lower()
    bucket = get_bucket()
    order = load_order(bucket, order_id)

    if order.get("status") == "paid":
        expires_at = int(order.get("downloadExpiresAt") or 0)
        if expires_at <= now_ts():
            raise ApiError(410, "DOWNLOAD_EXPIRED", "下载链接已过期")
        return {
            "orderId": order_id,
            "status": "PAID",
            "downloadUrl": sign_download_url(bucket, fmt, expires_at),
            "downloadExpiresAt": expires_at,
        }

    ensure_tx_unused(bucket, tx_hash, order_id)
    tx_meta = verify_transaction(order, tx_hash)
    paid_at = now_ts()
    expires_at = paid_at + DOWNLOAD_EXPIRES_SECONDS
    order.update(
        {
            "status": "paid",
            "format": fmt,
            "txHash": tx_hash,
            "paidAt": paid_at,
            "downloadExpiresAt": expires_at,
            **tx_meta,
        }
    )
    write_json_object(bucket, order_key(order_id), order)
    write_json_object(bucket, tx_key(tx_hash), {"orderId": order_id, "paidAt": paid_at})
    return {
        "orderId": order_id,
        "status": "PAID",
        "downloadUrl": sign_download_url(bucket, fmt, expires_at),
        "downloadExpiresAt": expires_at,
        "confirmations": tx_meta["confirmations"],
    }


def download_order(order_id: str, body: dict[str, Any]) -> dict[str, Any]:
    fmt = pick_format(body.get("format"))
    bucket = get_bucket()
    order = load_order(bucket, order_id)
    if order.get("status") != "paid":
        raise ApiError(402, "NOT_PAID", "订单还没有完成付款核销")
    expires_at = int(order.get("downloadExpiresAt") or 0)
    if expires_at <= now_ts():
        raise ApiError(410, "DOWNLOAD_EXPIRED", "下载链接已过期")
    return {
        "orderId": order_id,
        "status": "PAID",
        "downloadUrl": sign_download_url(bucket, fmt, expires_at),
        "downloadExpiresAt": expires_at,
    }


def route(request: Request) -> dict[str, Any]:
    if request.method == "GET" and request.path == "/health":
        return {"ok": True, "priceMars": PRICE_MARS, "payTo": PAY_TO_DISPLAY}
    if request.method == "POST" and request.path == "/orders":
        return create_order(request.body)

    parts = [part for part in request.path.strip("/").split("/") if part]
    if len(parts) == 3 and parts[0] == "orders" and parts[2] == "verify" and request.method == "POST":
        return verify_order(parts[1], request.body)
    if len(parts) == 3 and parts[0] == "orders" and parts[2] == "download" and request.method == "GET":
        return download_order(parts[1], request.body)

    raise ApiError(404, "NOT_FOUND", "接口不存在")


def response_headers(content_type: str = "application/json; charset=utf-8") -> list[tuple[str, str]]:
    return [
        ("Content-Type", content_type),
        ("Access-Control-Allow-Origin", CORS_ORIGIN),
        ("Access-Control-Allow-Headers", "content-type"),
        ("Access-Control-Allow-Methods", "GET,POST,OPTIONS"),
        ("Cache-Control", "no-store"),
    ]


def handler(environ: dict[str, Any], start_response: Callable[[str, list[tuple[str, str]]], None]) -> list[bytes]:
    if (environ.get("REQUEST_METHOD") or "").upper() == "OPTIONS":
        start_response("204 No Content", response_headers())
        return [b""]

    try:
        request = parse_request(environ)
        payload = route(request)
        status = "200 OK"
    except ApiError as exc:
        status = f"{exc.status} Error"
        payload = {"error": exc.code, "message": exc.message, **exc.extra}
    except Exception as exc:
        status = "500 Error"
        payload = {"error": "SERVER_ERROR", "message": str(exc)}

    start_response(status, response_headers())
    return [json.dumps(payload, ensure_ascii=False).encode("utf-8")]


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    port = int(os.getenv("PORT", "8787"))
    with make_server("127.0.0.1", port, handler) as server:
        print(f"paid download API listening on http://127.0.0.1:{port}{ROUTE_PREFIX}")
        server.serve_forever()
