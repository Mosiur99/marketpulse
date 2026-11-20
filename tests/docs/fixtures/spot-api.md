# Table of Contents

- [Exchange API](#exchange-api)
  - [Limits](#limits)
  - [Market data](#market-data)

# Exchange API

This sample reference describes a small spot exchange API used by the MarketPulse tests.

## Limits

Every request has a weight. The weights of all requests from one IP address are added up per
minute, and the total may not exceed 6000. A request over the limit receives HTTP 429.

## Market data

### Order book
```
GET /api/v3/depth
```
Returns the bids and asks for a symbol.

**Weight:** depends on the limit: 5 for up to 100 levels, 25 for up to 500 levels.

### Recent trades list
```
GET /api/v3/trades
```
The latest trades for a symbol, newest last.

**Weight:** 25

### Kline/Candlestick data
```
GET /api/v3/klines
```
Kline/candlestick bars for a symbol. Bars are identified by their open time.

**Weight:** 2

**Parameters:** symbol, interval, startTime, endTime, limit (default 500, maximum 1000).

### Symbol price ticker
```
GET /api/v3/ticker/price
```
The latest price for one or more symbols.

**Weight:** 2 for one symbol, 4 for all symbols.

## Signing requests

Signed endpoints need an HMAC SHA256 signature of the query string.

```shell
# Sign the request
echo -n "symbol=BTCUSDT&timestamp=1700000000000" | openssl dgst -sha256 -hmac "$SECRET"
```
