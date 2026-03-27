import json
from datetime import datetime, timezone
import sys

# Raw JSON string copied from Yahoo Finance chart API response
raw = r'''{"chart":{"result":[{"meta":{"currency":"TWD","symbol":"2344.TW","exchangeName":"TAI","fullExchangeName":"Taiwan","instrumentType":"EQUITY","firstTradeDate":946947600,"regularMarketTime":1774503001,"hasPrePostMarketData":false,"gmtoffset":28800,"timezone":"CST","exchangeTimezoneName":"Asia/Taipei","regularMarketPrice":96.0,"fiftyTwoWeekHigh":136.0,"fiftyTwoWeekLow":13.6,"regularMarketDayHigh":104.5,"regularMarketDayLow":96.0,"regularMarketVolume":189723274,"longName":"Winbond Electronics Corporation","shortName":"WINBOND ELECTRONIC CORP","chartPreviousClose":109.0,"priceHint":2,"currentTradingPeriod":{"pre":{"timezone":"CST","start":1774486800,"end":1774486800,"gmtoffset":28800},"regular":{"timezone":"CST","start":1774486800,"end":1774503000,"gmtoffset":28800},"post":{"timezone":"CST","start":1774503000,"end":1774503000,"gmtoffset":28800}},"dataGranularity":"1d","range":"10d","validRanges":["1d","5d","1mo","3mo","6mo","1y","2y","5y","10y","ytd","max"]},"timestamp":[1773363600,1773622800,1773709200,1773795600,1773882000,1773968400,1774227600,1774314000,1774400400,1774486800,1774503001],"indicators":{"quote":[{"open":[109.0,113.5,121.5,129.0,123.5,124.0,103.5,109.5,99.0,100.0,100.0],"low":[108.0,113.0,119.5,120.5,120.0,110.0,103.5,97.0,98.0999984741211,96.0,96.0],"close":[109.0,117.0,123.5,128.0,122.0,110.0,107.5,99.5,98.19999694824219,null,96.0],"high":[114.5,118.0,126.0,129.5,125.5,124.5,109.5,109.5,103.0,104.5,104.5],"volume":[171591816,199547521,234411445,294009796,196236526,333969259,146975829,219474874,197568906,189723274,189723274]}],"adjclose":[{"adjclose":[109.0,117.0,123.5,128.0,122.0,110.0,107.5,99.5,98.19999694824219,null,96.0]}]}}],"error":null}}'''

# Parse the top-level JSON (the string itself is JSON text)
obj = json.loads(raw)
res = obj["chart"]["result"][0]

timestamps = res.get("timestamp", [])
closes = res["indicators"]["quote"][0]["close"]

# Pair timestamps with closes, filter out nulls
pairs = []
for ts, c in zip(timestamps, closes):
    if c is None:
        continue
    pairs.append((ts, float(c)))

# Keep the last 5 trading days
last5 = pairs[-5:]

# Convert timestamps (epoch seconds) to Asia/Taipei date strings
# Yahoo's timestamps are in UTC epoch seconds; we'll convert to Asia/Taipei (+8)
from datetime import timezone, timedelta

tz_taipei = timezone(timedelta(hours=8))
labels = []
values = []
for ts, c in last5:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz_taipei)
    labels.append(dt.strftime('%Y-%m-%d'))
    values.append(round(c, 2))

out = {"labels": labels, "values": values}
print(json.dumps(out))
