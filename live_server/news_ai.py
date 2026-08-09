
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
would reasonably care about. Prefer the most recent 24 hours. If no material item exists, return NONE.
Do not invent news, dates, URLs, analyst actions, or catalysts. A rising/falling price alone is NOT a catalyst.

For EACH symbol return a single best catalyst assessment with:
- catalyst_strength: NONE, LOW, MEDIUM, HIGH, CRITICAL
- catalyst_type:
  NONE, EARNINGS_GUIDANCE, CONTRACT_CUSTOMER, MNA, FDA_REGULATORY, ANALYST,
  FINANCING_DILUTION, PRODUCT, LITIGATION, POLICY_MACRO, SECTOR, OTHER
- news_bias: BULLISH, BEARISH, MIXED, NEUTRAL
- news_long_power and news_short_power: integers summing to 100
- ai_confidence: LOW, MEDIUM, HIGH
- confidence_score: integer 0-100
- price_reaction: CONFIRMED, DIVERGENT, UNKNOWN
- source_quality: PRIMARY, TIER1, TIER2, OTHER, NONE
- event_recency: TODAY, ONE_DAY, TWO_THREE_DAYS, OLDER, UNKNOWN
- impact_horizon: INTRADAY, ONE_THREE_DAYS, ONE_TWO_WEEKS, LONGER, NONE
- event_time_utc: ISO-like UTC timestamp if confidently available, otherwise UNKNOWN
- source_title: title/outlet for the strongest supporting source, otherwise empty string
- source_url: strongest supporting source URL, otherwise empty string
- headline_ko: one-line Korean catalyst headline
- why_now_ko: one concise Korean sentence explaining why this matters NOW for a short-term trader
- summary_ko: concise Korean explanation, max 2 sentences
- risk_ko: concise Korean caveat, max 1 sentence

Source-quality guidance:
- PRIMARY: company IR/SEC filing, regulator/government, exchange, official corporate announcement
- TIER1: major financial newswire/newspaper with direct reporting
- TIER2: established finance publication/analyst summary
- OTHER: lower-confidence secondary source
- NONE: no material catalyst

Rules:
1. Earnings/guidance, major contract/customer, M&A, FDA/regulatory, litigation, financing/dilution,
   material analyst action, product launch, government policy directly affecting the company,
   or sector-wide semiconductor/AI events can be catalysts.
2. Rumors/social chatter without credible confirmation must be LOW, source_quality OTHER, and clearly identified.
3. Analyst target-price changes alone are usually LOW/MEDIUM unless unusually material and from a credible source.
4. If no material recent news exists: strength NONE, type NONE, bias NEUTRAL, 50/50,
   confidence LOW, confidence_score <= 50, source_quality NONE, impact_horizon NONE.
5. Use source_url only when supported by the web-search result. Never fabricate a URL.
6. Do not give personalized financial advice or certainty language.
7. catalyst_type MUST match the actual news item. If the article is about an acquisition, do not label it earnings/guidance.
8. If source_quality is PRIMARY, source_url SHOULD be present and must point to the official/primary source whenever available.
9. If a credible source URL cannot be established, set source_quality one tier lower rather than claiming PRIMARY without evidence.
10. When credible recent sources materially disagree, set news_bias=MIXED and explain the conflict in conflict_ko.
11. evidence_check must be PASS only when catalyst_type, bias, source quality, title/url, and summary are internally consistent.
12. Never fabricate timestamps or URLs. UNKNOWN/empty is preferable to an invented value.
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
                        "catalyst_type":{"type":"string","enum":[
                            "NONE","EARNINGS_GUIDANCE","CONTRACT_CUSTOMER","MNA","FDA_REGULATORY","ANALYST",
                            "FINANCING_DILUTION","PRODUCT","LITIGATION","POLICY_MACRO","SECTOR","OTHER"
                        ]},
                        "news_bias":{"type":"string","enum":["BULLISH","BEARISH","MIXED","NEUTRAL"]},
                        "news_long_power":{"type":"integer","minimum":0,"maximum":100},
                        "news_short_power":{"type":"integer","minimum":0,"maximum":100},
                        "ai_confidence":{"type":"string","enum":["LOW","MEDIUM","HIGH"]},
                        "confidence_score":{"type":"integer","minimum":0,"maximum":100},
                        "price_reaction":{"type":"string","enum":["CONFIRMED","DIVERGENT","UNKNOWN"]},
                        "source_quality":{"type":"string","enum":["PRIMARY","TIER1","TIER2","OTHER","NONE"]},
                        "event_recency":{"type":"string","enum":["TODAY","ONE_DAY","TWO_THREE_DAYS","OLDER","UNKNOWN"]},
                        "impact_horizon":{"type":"string","enum":["INTRADAY","ONE_THREE_DAYS","ONE_TWO_WEEKS","LONGER","NONE"]},
                        "event_time_utc":{"type":"string"},
                        "source_title":{"type":"string"},
                        "source_url":{"type":"string"},
                        "headline_ko":{"type":"string"},
                        "why_now_ko":{"type":"string"},
                        "summary_ko":{"type":"string"},
                        "risk_ko":{"type":"string"},
                        "evidence_check":{"type":"string","enum":["PASS","WARN","FAIL"]},
                        "evidence_warning":{"type":"string"},
                        "conflict_ko":{"type":"string"}
                    },
                    "required":[
                        "symbol","catalyst_strength","catalyst_type","news_bias",
                        "news_long_power","news_short_power","ai_confidence","confidence_score",
                        "price_reaction","source_quality","event_recency","impact_horizon",
                        "event_time_utc","source_title","source_url","headline_ko","why_now_ko",
                        "summary_ko","risk_ko","evidence_check","evidence_warning","conflict_ko"
                    ],
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
        try:
            x["confidence_score"]=max(0,min(100,int(x.get("confidence_score") or 0)))
        except Exception:
            x["confidence_score"]=0
        if not str(x.get("source_url") or "").startswith(("http://","https://")):
            x["source_url"]=""
        # Evidence consistency guardrails
        warnings=[]
        if x.get("source_quality")=="PRIMARY" and not x.get("source_url"):
            warnings.append("PRIMARY_WITHOUT_URL")
            # degrade rather than overstate evidence quality
            x["source_quality"]="TIER1"
        if x.get("catalyst_strength")=="NONE":
            x["catalyst_type"]="NONE"
            x["news_bias"]="NEUTRAL"
        if x.get("news_bias")=="MIXED" and not x.get("conflict_ko"):
            warnings.append("MIXED_WITHOUT_CONFLICT_NOTE")
        if warnings:
            x["evidence_check"]="WARN" if x.get("evidence_check")!="FAIL" else "FAIL"
            prior=str(x.get("evidence_warning") or "").strip()
            x["evidence_warning"]="; ".join(([prior] if prior else [])+warnings)
        items[sym]=x

    return {
        "enabled":True,
        "provider":"OPENAI_WEB_SEARCH",
        "model":model,
        "response_id":payload.get("id"),
        "sources":_extract_sources(payload),
        "items":items
    }


def analyze_news_resilient(symbols:list[str], market_context:dict, progress_cb=None) -> dict:
    """Analyze TOP5 one symbol at a time.

    This avoids one large web-search request holding the whole briefing hostage.
    A timeout/error on one symbol does not erase successful results from the others.
    progress_cb(done,total,symbol,status) is optional and never allowed to break the job.
    """
    symbols=[str(x).upper() for x in symbols if x][:5]
    merged={
        "enabled":bool(os.getenv("OPENAI_API_KEY","").strip()),
        "provider":"OPENAI_WEB_SEARCH",
        "model":os.getenv("DAYTRADER_NEWS_AI_MODEL","gpt-5").strip() or "gpt-5",
        "items":{},
        "sources":[],
        "symbol_status":{},
        "errors":{}
    }
    if not merged["enabled"]:
        merged["reason"]="OPENAI_API_KEY_NOT_CONFIGURED"
        return merged

    total=max(1,len(symbols))
    for idx,sym in enumerate(symbols,1):
        try:
            if progress_cb:
                try: progress_cb(idx-1,total,sym,"START")
                except Exception: pass
            one=analyze_news_with_openai([sym],market_context)
            if one.get("items",{}).get(sym):
                merged["items"][sym]=one["items"][sym]
                merged["symbol_status"][sym]="OK"
            else:
                merged["symbol_status"][sym]="NO_RESULT"
            for s in one.get("sources") or []:
                if s not in merged["sources"]:
                    merged["sources"].append(s)
            if one.get("error"):
                merged["errors"][sym]=one.get("error")
                merged["symbol_status"][sym]="ERROR"
        except Exception as e:
            merged["errors"][sym]=str(e)
            merged["symbol_status"][sym]="ERROR"
        finally:
            if progress_cb:
                try: progress_cb(idx,total,sym,merged["symbol_status"].get(sym,"DONE"))
                except Exception: pass

    if merged["errors"]:
        merged["error"]="; ".join(f"{k}: {v}" for k,v in merged["errors"].items())
    return merged

def combine_technical_and_news(row:dict, news:dict|None, premarket_live:bool) -> dict:
    tech_long=float(row.get("long_power") or 50)
    if not news:
        return {
            "final_long_power":round(tech_long,1),
            "final_short_power":round(100-tech_long,1),
            "final_signal":row.get("recommendation") or "WATCH",
            "news_weight":0.0,
            "news_weight_pct":0.0,
            "news_delta_long":0.0,
            "tech_long_before_news":round(tech_long,1),
            "news_long_input":50.0
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
        "news_weight":round(nw,3),
        "news_weight_pct":round(nw*100,1),
        "news_delta_long":round(final-tech_long,1),
        "tech_long_before_news":round(tech_long,1),
        "news_long_input":round(news_long,1)
    }
