#!/usr/bin/env python3
"""거래소에서 과거 OHLCV 데이터를 다운로드하여 CSV로 저장한다.

Usage:
    python scripts/download_ohlcv.py --symbol BTC/USDT --timeframe 1h --days 180
    python scripts/download_ohlcv.py --symbol ETH/USDT --timeframe 4h --days 365

출력: data/ohlcv/{symbol}_{timeframe}.csv

네트워크 필요: 이 스크립트만 실행 시 필요. 이후 백테스트는 오프라인 가능.
거래소 API 키 불필요: 공개 OHLCV 데이터는 인증 없이 조회 가능.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

# ccxt를 직접 사용 (ExchangeClient 의존성 회피)
try:
    import ccxt.async_support as ccxt
except ImportError:
    print("ERROR: ccxt not installed. Run: pip install ccxt")
    sys.exit(1)


async def download(
    symbol: str,
    timeframe: str,
    days: int,
    exchange_id: str,
    output_dir: Path,
) -> Path:
    """OHLCV 데이터를 다운로드하여 CSV로 저장한다."""
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        print(f"ERROR: Unknown exchange: {exchange_id}")
        sys.exit(1)

    exchange = exchange_class({"enableRateLimit": True})
    await exchange.load_markets()

    if symbol not in exchange.markets:
        await exchange.close()
        print(f"ERROR: Symbol {symbol} not found on {exchange_id}")
        sys.exit(1)

    # 시간 범위 계산
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    since_ms = now_ms - days * 24 * 60 * 60 * 1000

    tf_minutes = {
        "1m": 1, "5m": 5, "15m": 15, "30m": 30,
        "1h": 60, "2h": 120, "4h": 240,
        "1d": 1440, "1w": 10080,
    }
    minutes = tf_minutes.get(timeframe, 60)
    batch_size = 1000
    batch_ms = batch_size * minutes * 60 * 1000

    all_candles: list[list] = []
    cursor = since_ms

    print(f"Downloading {symbol} {timeframe} from {exchange_id} ({days} days)...")

    while cursor < now_ms:
        try:
            candles = await exchange.fetch_ohlcv(
                symbol, timeframe, since=cursor, limit=batch_size
            )
            if not candles:
                break

            all_candles.extend(candles)
            last_ts = candles[-1][0]
            cursor = last_ts + minutes * 60 * 1000

            pct = min(100, (cursor - since_ms) / (now_ms - since_ms) * 100)
            print(f"  {len(all_candles)} candles ({pct:.0f}%)", end="\r")

            if len(candles) < batch_size:
                break
        except Exception as e:
            print(f"\n  Warning: {e}, retrying...")
            await asyncio.sleep(2)

    await exchange.close()

    if not all_candles:
        print("\nNo data downloaded.")
        sys.exit(1)

    # 중복 제거 + 정렬
    seen = set()
    unique = []
    for c in all_candles:
        ts = c[0]
        if ts not in seen:
            seen.add(ts)
            unique.append(c)
    unique.sort(key=lambda c: c[0])

    # CSV 저장
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.replace("/", "_")
    filepath = output_dir / f"{safe_symbol}_{timeframe}.csv"

    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in unique:
            ts = datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc).isoformat()
            writer.writerow([ts, c[1], c[2], c[3], c[4], c[5]])

    print(f"\nSaved {len(unique)} candles → {filepath}")
    return filepath


def main() -> None:
    parser = argparse.ArgumentParser(description="Download OHLCV data to CSV")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair (e.g. BTC/USDT)")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe (e.g. 1h, 4h, 1d)")
    parser.add_argument("--days", type=int, default=180, help="Number of days to download")
    parser.add_argument("--exchange", default="binance", help="Exchange (default: binance)")
    parser.add_argument("--output", default="data/ohlcv", help="Output directory")
    args = parser.parse_args()

    asyncio.run(download(
        symbol=args.symbol,
        timeframe=args.timeframe,
        days=args.days,
        exchange_id=args.exchange,
        output_dir=Path(args.output),
    ))


if __name__ == "__main__":
    main()
