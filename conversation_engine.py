"""
POI Chat Conversation Engine
Handles multi-turn conversation state, intent detection, and response generation.
"""

import re
from dashscope import Generation

# Reuse the same whitelist from app.py
POI_WHITELIST = [
    "咖啡店", "奶茶店", "餐厅", "快餐店", "小吃店", "火锅店", "面包店",
    "超市", "便利店", "药店", "医院", "诊所", "社区卫生服务中心",
    "银行", "ATM", "加油站", "充电站", "停车场", "公交站", "地铁站",
    "火车站", "高铁站", "机场", "酒店", "宾馆", "电影院", "KTV",
    "健身房", "体育馆", "图书馆", "书店", "学校", "小学", "中学", "大学",
    "派出所", "消防站", "邮局", "快递点", "菜市场", "商场", "购物中心"
]

# Broad category → specific whitelist keywords
CATEGORY_MAP = {
    "餐饮类": ["餐厅", "快餐店", "小吃店", "火锅店", "咖啡店", "奶茶店", "面包店"],
    "教育类": ["学校", "小学", "中学", "大学", "图书馆", "书店"],
    "交通类": ["公交站", "地铁站", "火车站", "高铁站", "机场", "停车场", "加油站", "充电站"],
    "购物类": ["超市", "便利店", "商场", "购物中心", "菜市场"],
    "住宿类": ["酒店", "宾馆"],
    "医疗类": ["医院", "诊所", "药店", "社区卫生服务中心"],
    "娱乐类": ["电影院", "KTV", "健身房", "体育馆"],
    "金融类": ["银行", "ATM"],
    "生活服务类": ["邮局", "快递点", "派出所", "消防站"],
}

# User-friendly aliases → category label
CATEGORY_ALIASES = {
    "吃饭": "餐饮类", "吃的": "餐饮类", "美食": "餐饮类", "饮食": "餐饮类",
    "喝": "餐饮类", "饮品": "餐饮类", "饭店": "餐饮类", "用餐": "餐饮类",
    "教育": "教育类", "上学": "教育类", "读书": "教育类", "学习": "教育类", "培训": "教育类",
    "交通": "交通类", "出行": "交通类", "坐车": "交通类", "乘车": "交通类",
    "购物": "购物类", "买东西": "购物类", "逛街": "购物类", "买": "购物类", "菜场": "购物类",
    "住宿": "住宿类", "住": "住宿类", "睡觉": "住宿类", "旅馆": "住宿类",
    "医疗": "医疗类", "看病": "医疗类", "健康": "医疗类", "买药": "医疗类",
    "娱乐": "娱乐类", "玩": "娱乐类", "休闲": "娱乐类", "唱歌": "娱乐类", "看电影": "娱乐类",
    "金融": "金融类", "取钱": "金融类", "钱": "金融类", "存钱": "金融类",
    "生活": "生活服务类", "服务": "生活服务类", "办事": "生活服务类", "快递": "生活服务类",
}

# Terms indicating user didn't specify a concrete POI type
VAGUE_POI_TERMS = ["poi", "POI", "Poi", "附近有什么", "周边有什么", "有什么",
                    "找一下", "查一下", "搜一下", "看看", "查找", "搜索"]

# Greeting patterns
GREETING_PATTERNS = ["你好", "hi", "hello", "嗨", "在吗", "帮助", "help",
                     "谢谢", "thanks", "thank you", "您好", "哈喽"]


def detect_category(user_query):
    """Check if query contains a category label or alias. Returns (category_name, keywords) or (None, None)."""
    # Direct category match
    for cat_name in CATEGORY_MAP:
        if cat_name in user_query:
            return cat_name, CATEGORY_MAP[cat_name]
    # Alias match
    for alias, cat_name in CATEGORY_ALIASES.items():
        if alias in user_query:
            return cat_name, CATEGORY_MAP[cat_name]
    return None, None


def detect_explicit_poi_types(user_query):
    """Extract whitelist POI keywords directly mentioned in the query."""
    found = []
    for kw in POI_WHITELIST:
        if kw in user_query:
            found.append(kw)
    return found


def detect_location_suffix(user_query):
    """Extract location description from query using common Chinese location patterns."""
    patterns = [
        r'(.+?)附近', r'(.+?)周边', r'(.+?)周围', r'(.+?)旁边',
        r'(.+?)一带', r'(.+?)区域', r'(.+?)那边', r'(.+?)这里',
    ]
    for pattern in patterns:
        match = re.search(pattern, user_query)
        if match:
            loc = match.group(1).strip()
            # Filter out generic terms
            if loc and loc not in ["这", "那", "这个", "那个", "我"]:
                return loc
    return None


def is_greeting(user_query):
    """Check if the query is a greeting / non-search message."""
    # Don't treat count responses as greetings
    if detect_count(user_query):
        return False
    query_lower = user_query.lower().strip()
    for pat in GREETING_PATTERNS:
        if pat in query_lower:
            return True
    # Very short messages that aren't obviously a search
    if len(user_query) <= 2 and not any(kw in user_query for kw in POI_WHITELIST):
        return True
    return False


def detect_count(user_query):
    """Extract requested result count from query. Returns int or None."""
    match = re.search(r'(\d+)\s*[条个家]?', user_query)
    if match:
        count = int(match.group(1))
        if 1 <= count <= 100:
            return count
    return None


def detect_intent(user_query, context):
    """
    Analyze user intent using rule-based detection.
    Returns dict with: intent, has_location, has_poi_type, keywords, location_desc, category_label, target_count
    """
    result = {
        "intent": "unknown",
        "has_location": False,
        "has_poi_type": False,
        "keywords": [],
        "location_desc": "",
        "category_label": None,
        "confidence": "high",
        "target_count": None,
    }

    saved_location = context.get("last_location_desc", "")
    saved_keywords = context.get("last_keywords", [])
    state = context.get("state", "idle")

    # Check for count in query
    result["target_count"] = detect_count(user_query)

    # Step 1: Check for greetings
    if is_greeting(user_query) and state == "idle":
        result["intent"] = "greeting"
        return result

    # Step 2: Check for category labels
    cat_name, cat_keywords = detect_category(user_query)
    if cat_name:
        result["has_poi_type"] = True
        result["keywords"] = cat_keywords
        result["category_label"] = cat_name

    # Step 3: Check for explicit whitelist keywords
    explicit_types = detect_explicit_poi_types(user_query)
    if explicit_types:
        result["has_poi_type"] = True
        if not result["keywords"]:
            result["keywords"] = explicit_types
        else:
            result["keywords"] = explicit_types

    # Step 4: Check for location
    location_desc = detect_location_suffix(user_query)
    if location_desc:
        result["has_location"] = True
        result["location_desc"] = location_desc

    # Also check if context provides location
    if not result["has_location"] and saved_location:
        result["has_location"] = True
        result["location_desc"] = saved_location

    # Step 5: Determine intent
    if result["has_location"] and result["has_poi_type"]:
        result["intent"] = "full_search"
    elif result["has_location"] and not result["has_poi_type"]:
        result["intent"] = "need_type"
    elif not result["has_location"] and result["has_poi_type"]:
        result["intent"] = "need_location"
    elif not result["has_location"] and not result["has_poi_type"]:
        loc = detect_location_suffix(user_query)
        if loc:
            result["has_location"] = True
            result["location_desc"] = loc
            result["intent"] = "need_type"
        else:
            result["confidence"] = "low"
            result["intent"] = "ambiguous"

    return result


def detect_intent_with_llm(user_query, context):
    """LLM-based intent detection for ambiguous queries."""
    last_city = context.get("last_city", "南京")
    saved_location = context.get("last_location_desc", "")

    whitelist_str = "、".join(POI_WHITELIST)
    cat_names = "、".join(CATEGORY_MAP.keys())

    prompt = f"""
你是一个对话意图分析助手。分析用户消息，输出纯JSON。
字段：
- intent: "full_search"|"need_type"|"need_location"|"greeting"
- has_location: true/false（用户是否提到了具体地点）
- has_poi_type: true/false（用户是否指定了POI类型）
- location_desc: 地点描述字符串（如"南京信息工程大学"），无则为空
- keywords: 从白名单提取的POI类型数组
- category_label: 如果用户说的是大类（{cat_names}），填对应大类名，否则为空

POI白名单：{whitelist_str}
当前上下文城市：{last_city}
已保存地点：{saved_location or "无"}

规则：
- 用户只说了地点（如"南信大"、"新街口"）→ intent="need_type"
- 用户只说了类型（如"餐厅"、"咖啡店"）→ intent="need_location"
- 用户说了地点+类型 → intent="full_search"
- 用户说"poi"、"附近有什么"等模糊词+地点 → intent="need_type"
- 问好/感谢 → intent="greeting"

用户消息："{user_query}"
输出（仅纯JSON）：
"""
    try:
        response = Generation.call(
            model="qwen-plus",
            prompt=prompt,
            result_format="message",
            temperature=0.1
        )
        raw = response.output.choices[0].message.content.strip()
        json_match = re.search(r"[{].*[}]", raw, re.DOTALL)
        if json_match:
            parsed = eval(json_match.group(0))
            parsed["confidence"] = "medium"
            return parsed
    except Exception as e:
        print(f"[Intent LLM 失败] {e}")

    return {"intent": "greeting", "has_location": False, "has_poi_type": False,
            "keywords": [], "location_desc": "", "category_label": None, "confidence": "low"}


def get_follow_up_question(context, missing):
    """Generate a follow-up question based on what's missing."""
    location_desc = context.get("last_location_desc", "")

    if missing == "type":
        loc_text = f"“{location_desc}”" if location_desc else "该区域"
        return {
            "message": f"请问你想在{loc_text}附近查找什么类型的POI呢？",
            "quick_replies": [
                {"label": "🍽 餐饮类", "payload": "餐饮类"},
                {"label": "📚 教育类", "payload": "教育类"},
                {"label": "🚇 交通类", "payload": "交通类"},
                {"label": "🛒 购物类", "payload": "购物类"},
                {"label": "🏨 住宿类", "payload": "住宿类"},
                {"label": "🏥 医疗类", "payload": "医疗类"},
                {"label": "🎬 娱乐类", "payload": "娱乐类"},
                {"label": "🏦 金融类", "payload": "金融类"},
                {"label": "📮 生活服务类", "payload": "生活服务类"},
            ]
        }
    elif missing == "location":
        return {
            "message": "请问你想在哪个地点附近查找呢？可以告诉我具体的地名或地址。",
            "quick_replies": []
        }
    elif missing == "count":
        cat_label = context.get("last_category", "")
        type_desc = cat_label if cat_label else "、".join(context.get("last_keywords", [])[:3])
        loc_desc = context.get("last_location_desc", "")
        loc_short = loc_desc[:20] if len(loc_desc) > 20 else loc_desc
        return {
            "message": f"已确认：在{loc_short}附近查找{type_desc}类POI。\n请问需要返回多少条结果？（建议10条，最多100条）",
            "quick_replies": [
                {"label": "5条", "payload": "5条"},
                {"label": "10条", "payload": "10条"},
                {"label": "20条", "payload": "20条"},
                {"label": "30条", "payload": "30条"},
                {"label": "50条", "payload": "50条"},
            ]
        }
    else:
        return {
            "message": "你好！我是POI地理信息查询助手。你可以直接告诉我你想查找什么，例如'南京信息工程大学附近的餐厅'。请问你想查找什么？",
            "quick_replies": [
                {"label": "🍽 餐饮类", "payload": "餐饮类"},
                {"label": "📚 教育类", "payload": "教育类"},
                {"label": "🚇 交通类", "payload": "交通类"},
                {"label": "🛒 购物类", "payload": "购物类"},
                {"label": "🏨 住宿类", "payload": "住宿类"},
                {"label": "🏥 医疗类", "payload": "医疗类"},
                {"label": "🎬 娱乐类", "payload": "娱乐类"},
            ]
        }


def process_chat_message(user_query, session_id, conversation_context,
                         parse_func, geocode_func, search_func, kde_func, insight_func):
    """
    Process a chat message and return the appropriate response.
    This is the main orchestrator called by the /chat endpoint.

    Parameters:
    - user_query: the user's message text
    - session_id: session identifier
    - conversation_context: the global context dict (modified in place)
    - parse_func: function to parse query (parse_query_with_context)
    - geocode_func: function to geocode address (geocode_address)
    - search_func: function to search POI (search_poi)
    - kde_func: function for KDE analysis (calculate_kde)
    - insight_func: function for density insight (generate_density_insight)

    Returns: dict ready for jsonify
    """
    DEFAULT_CITY = "南京"

    # Load or init context
    if session_id not in conversation_context:
        conversation_context[session_id] = {
            "state": "idle",
            "last_city": DEFAULT_CITY,
            "last_keywords": [],
            "last_sub_keywords": [],
            "last_location_desc": "",
            "center": "",
        }
    ctx = conversation_context[session_id]

    # Step 1: Rule-based intent detection
    intent_result = detect_intent(user_query, ctx)

    # Step 2: LLM fallback for ambiguous queries
    if intent_result["confidence"] == "low":
        llm_result = detect_intent_with_llm(user_query, ctx)
        # Merge, preferring LLM result for ambiguous cases
        if llm_result.get("has_location"):
            intent_result["has_location"] = True
            intent_result["location_desc"] = llm_result.get("location_desc", "")
        if llm_result.get("has_poi_type"):
            intent_result["has_poi_type"] = True
            if llm_result.get("keywords"):
                intent_result["keywords"] = llm_result["keywords"]
            if llm_result.get("category_label"):
                intent_result["category_label"] = llm_result["category_label"]
        intent_result["intent"] = llm_result.get("intent", intent_result["intent"])

    # Step 3: Act based on intent
    intent = intent_result["intent"]
    state = ctx.get("state", "idle")

    # --- GREETING ---
    if intent == "greeting":
        q = get_follow_up_question(ctx, "greeting")
        return {
            "type": "follow_up",
            "message": q["message"],
            "quick_replies": q["quick_replies"],
            "context": {
                "session_id": session_id,
                "state": ctx["state"],
                "city": ctx["last_city"],
                "location_desc": ctx["last_location_desc"],
                "keywords": ctx["last_keywords"],
            }
        }

    # --- NEED TYPE: user specified location but no POI type ---
    if (intent == "need_type" or (state == "awaiting_type" and not intent_result["has_poi_type"])) and state != "awaiting_count":
        # Save location from query
        loc_desc = intent_result.get("location_desc", "")
        if loc_desc:
            ctx["last_location_desc"] = loc_desc
            # Try to geocode
            center = geocode_func(loc_desc, ctx["last_city"])
            if center:
                ctx["center"] = center
        ctx["state"] = "awaiting_type"
        q = get_follow_up_question(ctx, "type")
        return {
            "type": "follow_up",
            "message": q["message"],
            "quick_replies": q["quick_replies"],
            "context": {
                "session_id": session_id,
                "state": ctx["state"],
                "city": ctx["last_city"],
                "location_desc": ctx["last_location_desc"],
            }
        }

    # --- NEED LOCATION: user specified type but no location ---
    if intent == "need_location" and state != "awaiting_count":
        # Save keywords from query
        if intent_result.get("keywords"):
            ctx["last_keywords"] = intent_result["keywords"]
        if intent_result.get("category_label"):
            ctx["last_category"] = intent_result["category_label"]
        ctx["state"] = "awaiting_type"  # we have type, but need location too
        q = get_follow_up_question(ctx, "location")
        return {
            "type": "follow_up",
            "message": q["message"],
            "quick_replies": q["quick_replies"],
            "context": {
                "session_id": session_id,
                "state": ctx["state"],
                "city": ctx["last_city"],
                "location_desc": ctx["last_location_desc"],
                "keywords": ctx["last_keywords"],
            }
        }

    # --- AWAITING COUNT: user needs to specify result count ---
    if state == "awaiting_count":
        target_count = intent_result.get("target_count") or detect_count(user_query)
        if not target_count:
            # Re-ask for count
            q = get_follow_up_question(ctx, "count")
            return {
                "type": "follow_up",
                "message": q["message"],
                "quick_replies": q["quick_replies"],
                "context": {
                    "session_id": session_id,
                    "state": "awaiting_count",
                    "city": ctx["last_city"],
                    "location_desc": ctx["last_location_desc"],
                    "keywords": ctx["last_keywords"],
                }
            }
        # User gave a count — fix intent so subsequent blocks route correctly
        intent_result["intent"] = "full_search"
        intent_result["has_location"] = True
        intent_result["has_poi_type"] = True
        intent_result["keywords"] = ctx.get("last_keywords", [])
        intent_result["location_desc"] = ctx.get("last_location_desc", "")
        intent = "full_search"

    # --- FULL SEARCH or REFINE: we have both location and type ---
    if intent == "full_search" or intent == "ambiguous" or state == "awaiting_count":
        # Merge with saved context
        loc_desc = intent_result.get("location_desc", "") or ctx.get("last_location_desc", "")
        keywords = intent_result.get("keywords", [])
        if not keywords:
            keywords = ctx.get("last_keywords", [])

        if not loc_desc:
            q = get_follow_up_question(ctx, "location")
            return {
                "type": "follow_up",
                "message": q["message"],
                "quick_replies": q["quick_replies"],
                "context": {
                    "session_id": session_id,
                    "state": ctx["state"],
                    "city": ctx["last_city"],
                    "location_desc": ctx["last_location_desc"],
                }
            }

        if not keywords:
            ctx["last_location_desc"] = loc_desc
            ctx["state"] = "awaiting_type"
            q = get_follow_up_question(ctx, "type")
            return {
                "type": "follow_up",
                "message": q["message"],
                "quick_replies": q["quick_replies"],
                "context": {
                    "session_id": session_id,
                    "state": ctx["state"],
                    "city": ctx["last_city"],
                    "location_desc": loc_desc,
                }
            }

        # Determine target_count
        target_count = intent_result.get("target_count")
        if state == "awaiting_count":
            target_count = target_count or detect_count(user_query) or 10
        elif not target_count:
            # No count specified: ask user for count before searching
            ctx["last_location_desc"] = loc_desc
            ctx["last_keywords"] = keywords
            ctx["last_city"] = ctx["last_city"]
            ctx["last_category"] = intent_result.get("category_label", "")
            if intent_result.get("location_desc"):
                center = geocode_func(loc_desc, ctx["last_city"])
                if center:
                    ctx["center"] = center
            ctx["state"] = "awaiting_count"
            q = get_follow_up_question(ctx, "count")
            return {
                "type": "follow_up",
                "message": q["message"],
                "quick_replies": q["quick_replies"],
                "context": {
                    "session_id": session_id,
                    "state": "awaiting_count",
                    "city": ctx["last_city"],
                    "location_desc": loc_desc,
                    "keywords": keywords,
                }
            }

        # Parse for city info
        parsed = parse_func(user_query, ctx["last_city"])
        city = parsed.get("city", ctx["last_city"]).strip() or ctx["last_city"]

        # Geocode
        center = ctx.get("center", "")
        if loc_desc != ctx.get("last_location_desc", "") or not center:
            center = geocode_func(loc_desc, city)

        # Update context
        ctx["last_city"] = city
        ctx["last_location_desc"] = loc_desc
        ctx["last_keywords"] = keywords
        if center:
            ctx["center"] = center
        ctx["state"] = "has_context"

        # Search with target_count for progressive radius expansion
        pois, has_sub_flags = search_func(city, keywords, [], center, target_count=target_count)
        kde_data = kde_func(pois)
        insight = insight_func(pois)

        # Format results
        results = []
        for i, (poi, has_sub) in enumerate(zip(pois[:100], has_sub_flags[:100]), 1):
            name = poi.get("name", "")
            address = poi.get("address", "") or "暂无地址"
            phone = poi.get("phone", "") or "暂无"
            location = poi.get("location", "")
            lng, lat = location.split(",") if location else ("", "")
            pid = poi.get("id", "")
            ptype = poi.get("type", "未知")
            results.append({
                "id": i,
                "name": name,
                "address": address,
                "phone": phone,
                "lng": lng,
                "lat": lat,
                "detail_url": f"https://www.amap.com/place/{pid}" if pid else "#",
                "type": ptype,
                "has_sub_poi": has_sub
            })

        cat_label = intent_result.get("category_label", "")
        type_desc = cat_label if cat_label else "、".join(keywords[:3])
        loc_short = loc_desc[:20] if len(loc_desc) > 20 else loc_desc

        return {
            "type": "results",
            "message": f"已为您找到“{loc_short}”附近的{type_desc}类POI，共{len(results)}条结果。",
            "quick_replies": [
                {"label": "🔄 换一个类型", "payload": "换一个类型"},
                {"label": "📍 换一个地点", "payload": "换一个地点"},
                {"label": "🆕 重新查询", "payload": "重新查询"},
            ],
            "context": {
                "session_id": session_id,
                "state": ctx["state"],
                "city": city,
                "location_desc": loc_desc,
                "keywords": keywords,
            },
            "insight": insight,
            "kde": kde_data,
            "total": len(results),
            "results": results,
            "keywords": keywords,
        }

    # Fallback
    q = get_follow_up_question(ctx, "greeting")
    return {
        "type": "follow_up",
        "message": q["message"],
        "quick_replies": q["quick_replies"],
        "context": {
            "session_id": session_id,
            "state": ctx["state"],
            "city": ctx["last_city"],
            "location_desc": ctx["last_location_desc"],
        }
    }
