
from __future__ import annotations
import os, json, urllib.request, urllib.error
from datetime import datetime, timezone

OPENAI_URL = "https://api.openai.com/v1/responses"

NEWS_AI_TIMEOUT_SECONDS = int(os.getenv("DAYTRADER_NEWS_AI_TIMEOUT_SECONDS", "90"))
NEWS_AI_RETRY_TIMEOUT_SECONDS = int(os.getenv("DAYTRADER_NEWS_AI_RETRY_TIMEOUT_SECONDS", "150"))
NEWS_AI_MAX_ATTEMPTS = int(os.getenv("DAYTRADER_NEWS_AI_MAX_ATTEMPTS", "2"))


def _openai_request_with_retry(req):
    """Call OpenAI with a longer read timeout and retry once on timeout."""
    import socket
    last_err = None
    attempts = max(1, NEWS_AI_MAX_ATTEMPTS)
    for attempt in range(attempts):
        timeout_seconds = NEWS_AI_TIMEOUT_SECONDS if attempt == 0 else NEWS_AI_RETRY_TIMEOUT_SECONDS
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                last_err = e
            else:
                raise
        except (TimeoutError, socket.timeout) as e:
            last_err = e
        if attempt + 1 >= attempts:
            raise last_err
    raise last_err

def _extract_sources(payload:dict) -> list[dict]:
    seen=set(); out=[]
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            for ann in content.get("annotations") or []:
                if ann.get("type")=="url_citation":
                    url=ann.get("url")
                    title=ann.get("title") or url
                    if url and url not in seen:
                        seen.add(url); out.append({"title":title,"url":url})
    return out

def _response_text(payload:dict) -> str:
    if payload.get("output_text"):
        return payload["output_text"]
    chunks=[]
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type")=="output_text" and content.get("text"):
                chunks.append(content["text"])
    return "\n".join(chunks)

def analyze_news_with_openai(symbols:list[str], market_context:dict) -> dict:
    """
    Optional external intelligence layer.
    Uses OpenAI Responses API + built-in web_search when OPENAI_API_KEY is configured.
    Returns structured per-symbol news/catalyst judgement plus source citations.
    """
    key=os.getenv("OPENAI_API_KEY","").strip()
    if not key:
        return {
            "enabled":False,
            "provider":"OPENAI_WEB_SEARCH",
            "reason":"OPENAI_API_KEY_NOT_CONFIGURED",
            "items":{}
        }

    model=os.getenv("DAYTRADER_NEWS_AI_MODEL","gpt-5").strip() or "gpt-5"
    symbols=[str(x).upper() for x in symbols if x][:5]
    if not symbols:
        return {"enabled":True,"provider":"OPENAI_WEB_SEARCH","items":{}}

    now=datetime.now(timezone.utc).isoformat()
    prompt=f"""
You are a pre-market trading news analyst. Current UTC time: {now}.
Analyze ONLY these US-listed symbols: {', '.join(symbols)}.

Market context supplied by the trading system:
{json.dumps(market_context,ensure_ascii=False)}

Use web search to find material, recent company-specific or sector-specific news that a short-term trader
would reasonably care about. Prefer the most recent 24 hours; if there is no material item, say NONE.
Do not invent news. Separate price action from news: a rising price alone is not a catalyst.
For each symbol classify:
- catalyst_strength: NONE, LOW, MEDIUM, HIGH, CRITICAL
- news_bias: BULLISH, BEARISH, MIXED, NEUTRAL
- news_long_power and news_short_power, integers summing to 100
- ai_confidence: LOW, MEDIUM, HIGH
- price_reaction: CONFIRMED, DIVERGENT, UNKNOWN
- summary_ko: concise Korean explanation, max 2 sentences
- risk_ko: concise Korean caveat, max 1 sentence

Rules:
1. Earnings/guidance, major contract/customer, M&A, FDA/regulatory, litigation, financing/dilution,
   material analyst action, product launch, government policy directly affecting the company,
   or sector-wide semiconductor/AI events can be catalysts.
2. Rumors/social chatter without credible confirmation must be LOW and clearly identified.
3. If no material recent news exists, set catalyst_strength NONE, bias NEUTRAL, 50/50.
4. Do not give personalized financial advice or certainty language.
"""

    schema={
        "type":"object",
        "properties":{
            "items":{
                "type":"array",
                "items":{
                    "type":"object",
                    "properties":{
                        "symbol":{"type":"string"},
                        "catalyst_strength":{"type":"string","enum":["NONE","LOW","MEDIUM","HIGH","CRITICAL"]},
                        "news_bias":{"type":"string","enum":["BULLISH","BEARISH","MIXED","NEUTRAL"]},
                        "news_long_power":{"type":"integer","minimum":0,"maximum":100},
                        "news_short_power":{"type":"integer","minimum":0,"maximum":100},
                        "ai_confidence":{"type":"string","enum":["LOW","MEDIUM","HIGH"]},
                        "price_reaction":{"type":"string","enum":["CONFIRMED","DIVERGENT","UNKNOWN"]},
                        "summary_ko":{"type":"string"},
                        "risk_ko":{"type":"string"}
                    },
                    "required":["symbol","catalyst_strength","news_bias","news_long_power","news_short_power",
                                "ai_confidence","price_reaction","summary_ko","risk_ko"],
                    "additionalProperties":False
                }
            }
        },
        "required":["items"],
        "additionalProperties":False
    }
    body={
        "model":model,
        "store":False,
        "tools":[{"type":"web_search"}],
        "input":prompt,
        "text":{"format":{
            "type":"json_schema",
            "name":"preopen_news_catalyst",
            "strict":True,
            "schema":schema
        }}
    }

    req=urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
        method="POST"
    )
    try:
        payload=_openai_request_with_retry(req)
    except urllib.error.HTTPError as e:
        detail=e.read().decode("utf-8","replace")
        return {"enabled":True,"provider":"OPENAI_WEB_SEARCH","error":f"HTTP {e.code}: {detail[:1000]}","items":{}}
    except Exception as e:
        return {"enabled":True,"provider":"OPENAI_WEB_SEARCH","error":str(e),"items":{}}

    txt=_response_text(payload)
    try:
        parsed=json.loads(txt)
    except Exception:
        parsed={"items":[]}

    items={}
    for x in parsed.get("items") or []:
        sym=str(x.get("symbol") or "").upper()
        if not sym: continue
        lp=int(x.get("news_long_power") or 50)
        sp=int(x.get("news_short_power") or 50)
        if lp+sp!=100:
            sp=max(0,100-lp)
        x["news_long_power"]=lp; x["news_short_power"]=sp
        items[sym]=x

    return {
        "enabled":True,
        "provider":"OPENAI_WEB_SEARCH",
        "model":model,
        "response_id":payload.get("id"),
        "sources":_extract_sources(payload),
        "items":items
    }

def combine_technical_and_news(row:dict, news:dict|None, premarket_live:bool) -> dict:
    tech_long=float(row.get("long_power") or 50)
    if not news:
        return {
            "final_long_power":round(tech_long,1),
            "final_short_power":round(100-tech_long,1),
            "final_signal":row.get("recommendation") or "WATCH",
            "news_weight":0.0
        }

    strength=str(news.get("catalyst_strength") or "NONE").upper()
    confidence=str(news.get("ai_confidence") or "LOW").upper()
    nw={"NONE":0.0,"LOW":0.08,"MEDIUM":0.16,"HIGH":0.24,"CRITICAL":0.30}.get(strength,0.0)
    nw *= {"LOW":0.6,"MEDIUM":0.85,"HIGH":1.0}.get(confidence,0.6)
    # A verified live premarket reaction makes news more useful; stale price data does not.
    if not premarket_live:
        nw *= 0.75
    if str(news.get("price_reaction") or "UNKNOWN").upper()=="DIVERGENT":
        nw *= 0.65

    news_long=float(news.get("news_long_power") or 50)
    final=tech_long*(1-nw)+news_long*nw
    final=max(1,min(99,round(final,1)))
    if final>=82: sig="STRONG_LONG"
    elif final>=67: sig="LONG"
    elif final<=25: sig="STRONG_SHORT"
    elif final<=38: sig="SHORT"
    else: sig="WATCH"
    return {
        "final_long_power":final,
        "final_short_power":round(100-final,1),
        "final_signal":sig,
        "news_weight":round(nw,3)
    }
