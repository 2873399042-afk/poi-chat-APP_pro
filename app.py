
from flask import Flask, request, render_template, jsonify
import re
import requests
from dashscope import Generation
import dashscope
from math import radians, cos, sin, asin, sqrt
import numpy as np
from sklearn.neighbors import KernelDensity
from conversation_engine import process_chat_message

# 初始化 Flask 应用
app = Flask(__name__)
# === 配置 API Keys ===
DASHSCOPE_API_KEY = "sk-9c5f6447cd3b48deb6ee603141759be6"
AMAP_API_KEY = "3e1379e3c44e93990c9c31dd707b031e"
dashscope.api_key = DASHSCOPE_API_KEY
# 默认兜底城市
DEFAULT_CITY = "南京"
# 单页返回最大结果数
RESULT_LIMIT = 100
# 存储对话上下文
conversation_context = {}


# ========== 支持多POI类型的NLP解析 ==========
def parse_query_with_context(user_query, last_city=DEFAULT_CITY):
    """支持多POI类型并列解析，区分目标POI和附属筛选条件"""
    # POI白名单（保持原有不变）
    POI_WHITELIST = [
        "咖啡店", "奶茶店", "餐厅", "快餐店", "小吃店", "火锅店", "面包店",
        "超市", "便利店", "药店", "医院", "诊所", "社区卫生服务中心",
        "银行", "ATM", "加油站", "充电站", "停车场", "公交站", "地铁站",
        "火车站", "高铁站", "机场", "酒店", "宾馆", "电影院", "KTV",
        "健身房", "体育馆", "图书馆", "书店", "学校", "小学", "中学", "大学",
        "派出所", "消防站", "邮局", "快递点", "菜市场", "商场", "购物中心"
    ]
    whitelist_str = "、".join(POI_WHITELIST)

    # 支持多关键词解析
    prompt = f"""
你是一个地理信息助手，请输出纯JSON，仅包含字段：city, keywords, sub_keywords, location_desc。
字段严格定义：
1. city: 城市名称，用户未提及则使用上下文城市 {last_city}
2. keywords: 【必填】用户要查询的目标POI类型数组，必须从白名单中选择，支持多个并列类型
   - 例：用户说"奶茶店和咖啡店" → ["奶茶店", "咖啡店"]
   - 例：用户说"餐厅、酒店、便利店" → ["餐厅", "酒店", "便利店"]
   - 例：用户说"带有便利店的停车场" → ["停车场"]
3. sub_keywords: 附属筛选条件数组，必须从白名单中选择，无则为空数组
   - 例：用户说"带有便利店的停车场" → ["便利店"]
   - 例：用户说"带停车场的餐厅和酒店" → ["停车场"]
4. location_desc: 具体地点名（如"新街口""南京信息工程大学"），不要填"附近"

强制规则：
- keywords和sub_keywords中的每个元素，必须严格从以下白名单中选择：{whitelist_str}
- 禁止输出白名单以外的任何POI类型
- 若用户意图模糊，keywords默认填["餐厅"]，sub_keywords为空数组

用户查询: "{user_query}"
输出（仅纯JSON，无任何多余文字、注释、换行）:
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
            # 【校验】强制过滤白名单外的关键词
            valid_keywords = [kw for kw in parsed.get("keywords", []) if kw in POI_WHITELIST]
            parsed["keywords"] = valid_keywords if valid_keywords else ["餐厅"]
            parsed["sub_keywords"] = [kw for kw in parsed.get("sub_keywords", []) if kw in POI_WHITELIST]
            return parsed
        else:
            raise ValueError("No JSON found")
    except Exception as e:
        print(f"[Qwen 解析失败] {e}")
        # 兜底返回
        return {
            "city": last_city,
            "keywords": [extract_fallback_keyword(user_query)],
            "sub_keywords": [],
            "location_desc": ""
        }


def extract_fallback_keyword(query):
    """关键词回退策略"""
    query = query.lower()
    rules = {
        "喝": "咖啡店", "咖啡": "咖啡店", "奶茶": "奶茶店", "茶": "奶茶店",
        "吃": "餐厅", "饭": "餐厅", "菜": "餐厅", "火锅": "火锅店", "面": "餐厅",
        "药": "药店", "医": "医院", "病": "医院", "不舒服": "医院",
        "车": "停车场", "停": "停车场", "停车": "停车场", "加油": "加油站",
        "住": "酒店", "宿": "酒店", "旅馆": "酒店",
        "电影": "电影院", "玩": "KTV", "唱": "KTV", "歌": "KTV",
        "书": "书店", "银行": "银行", "取钱": "ATM", "存钱": "银行",
        "超": "超市", "便利": "便利店", "学": "学校", "校": "学校",
        "健身": "健身房", "锻炼": "健身房"
    }
    for word, kw in rules.items():
        if word in query:
            return kw
    return "餐厅"


def geocode_address(address, city):
    """地理编码（保持原有不变）"""
    url = "https://restapi.amap.com/v3/geocode/geo"
    params = {
        "key": AMAP_API_KEY,
        "address": address,
        "city": city,
        "output": "json"
    }
    try:
        resp = requests.get(url, params=params, timeout=5).json()
        if resp["status"] == "1" and resp["geocodes"]:
            return resp["geocodes"][0]["location"]
    except Exception as e:
        print(f"[Geocode 失败] {e}")
    return ""


# ========== 支持多关键词批量POI搜索（3km起，渐进扩圈） ==========
def search_poi(city, keywords, sub_keywords=[], center="", target_count=None):
    """
    多关键词POI搜索，支持渐进式半径扩展。
    :param city: 城市
    :param keywords: 目标POI类型数组
    :param sub_keywords: 附属筛选条件数组
    :param center: 中心坐标
    :param target_count: 目标返回条数，为None时不扩圈
    :return: 去重后的POI列表，按距离排序
    """
    # 渐进式半径（米）：从3km起，逐步扩大
    RADIUS_TIERS = [3000, 5000, 10000, 20000, 30000]
    DEFAULT_RADIUS = 3000  # "附近"默认3km

    # 同义词兜底映射
    synonym_map = {
        "奶茶店": ["饮品店", "茶饮"], "咖啡店": ["饮品店", "咖啡"],
        "餐厅": ["美食", "饭店", "餐饮"], "快餐店": ["美食", "简餐"],
        "小吃店": ["美食", "小吃"], "便利店": ["超市", "商店"],
        "药店": ["药房", "医药"], "医院": ["医疗机构", "诊所"],
        "充电站": ["充电桩", "新能源充电"], "地铁站": ["轨道交通", "地铁"],
        "火车站": ["铁路车站", "火车"], "学校": ["教育机构", "校区"],
        "停车场": ["停车区", "停车位"]
    }

    def _do_single_search(kw, use_around=True, radius=DEFAULT_RADIUS):
        if center and use_around:
            url = "https://restapi.amap.com/v3/place/around"
            params = {
                "key": AMAP_API_KEY,
                "keywords": kw,
                "location": center,
                "radius": radius,
                "offset": 100,
                "extensions": "all",
                "output": "json"
            }
        else:
            url = "https://restapi.amap.com/v3/place/text"
            params = {
                "key": AMAP_API_KEY,
                "keywords": kw,
                "city": city,
                "offset": 100,
                "extensions": "all",
                "output": "json"
            }
        try:
            resp = requests.get(url, params=params, timeout=5).json()
            if resp.get("status") == "1":
                return resp.get("pois", [])
        except Exception as e:
            print(f"[POI 搜索失败] 关键词:{kw}, 错误:{e}")
        return []

    def _collect_pois_for_radius(radius):
        """按指定半径搜索所有关键词，返回去重POI列表"""
        all_pois = []
        for kw in keywords:
            pois = _do_single_search(kw, use_around=bool(center), radius=radius)
            if not pois:
                print(f"[兜底触发] 关键词{kw}在{radius}m无结果，尝试同义词")
                for alt_kw in synonym_map.get(kw, ["餐厅", "商店"]):
                    pois = _do_single_search(alt_kw, use_around=bool(center), radius=radius)
                    if pois:
                        break
            all_pois.extend(pois)
        # 去重
        seen = set()
        unique = []
        for p in all_pois:
            pid = p.get("id", "")
            if pid and pid not in seen:
                seen.add(pid)
                unique.append(p)
        return unique

    # ========== 1. 搜索POI（渐进扩圈） ==========
    if target_count and center:
        # 渐进扩圈模式：从小到大尝试半径
        all_unique = []
        tried_radii = []
        for radius in RADIUS_TIERS:
            print(f"[渐进搜索] 半径={radius}m，当前已有{len(all_unique)}条，目标{target_count}条")
            batch = _collect_pois_for_radius(radius)
            # 合并去重
            exist_ids = {p.get("id") for p in all_unique}
            for p in batch:
                if p.get("id") not in exist_ids:
                    exist_ids.add(p.get("id"))
                    all_unique.append(p)
            tried_radii.append(radius)
            if len(all_unique) >= target_count:
                break
        unique_pois = all_unique
        used_radius = tried_radii[-1] if tried_radii else DEFAULT_RADIUS
        print(f"[渐进搜索完成] 最终半径={used_radius}m，共{len(unique_pois)}条")
    else:
        # 非扩圈模式：单次搜索
        unique_pois = _collect_pois_for_radius(DEFAULT_RADIUS)

    # ========== 2. 附属条件筛选 ==========
    def check_sub_poi_nearby(poi_location, sub_kw, radius=500):
        url = "https://restapi.amap.com/v3/place/around"
        params = {
            "key": AMAP_API_KEY, "keywords": sub_kw,
            "location": poi_location, "radius": radius, "offset": 1
        }
        try:
            resp = requests.get(url, params=params, timeout=3).json()
            return len(resp.get("pois", [])) > 0
        except:
            return False

    filtered_pois = []
    has_sub_flags = []
    for poi in unique_pois:
        poi_location = poi.get("location", "")
        if not poi_location:
            continue
        has_all_sub = True
        for sub_kw in sub_keywords:
            if not check_sub_poi_nearby(poi_location, sub_kw):
                has_all_sub = False
                break
        filtered_pois.append(poi)
        has_sub_flags.append(has_all_sub)

    # ========== 3. 按距离排序 ==========
    if center and filtered_pois:
        center_lng, center_lat = map(float, center.split(","))
        filtered_pois.sort(key=lambda p: haversine(
            center_lng, center_lat,
            float(p["location"].split(",")[0]), float(p["location"].split(",")[1])
        ))

    # ========== 4. 截断到目标条数 ==========
    limit = target_count if target_count else RESULT_LIMIT
    limit = min(limit, RESULT_LIMIT)
    return filtered_pois[:limit], has_sub_flags[:limit]


def haversine(lon1, lat1, lon2, lat2):
    """球面距离计算（保持原有不变）"""
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return c * 6371000


def calculate_kde(pois, bandwidth=0.01):
    """核密度计算（保持原有不变）"""
    if len(pois) < 2:
        return None
    coords = []
    for poi in pois:
        try:
            location = poi.get("location", "")
            if location and "," in location:
                lng, lat = map(float, location.split(","))
                coords.append([lng, lat])
        except:
            continue
    if len(coords) < 2:
        return None
    coords = np.array(coords)
    coords_rad = np.radians(coords)
    kde = KernelDensity(bandwidth=bandwidth, metric='haversine').fit(coords_rad)
    x_min, x_max = coords[:, 0].min() - 0.02, coords[:, 0].max() + 0.02
    y_min, y_max = coords[:, 1].min() - 0.02, coords[:, 1].max() + 0.02
    xx, yy = np.meshgrid(np.linspace(x_min, x_max, 50), np.linspace(y_min, y_max, 50))
    grid_coords = np.vstack([xx.ravel(), yy.ravel()]).T
    grid_coords_rad = np.radians(grid_coords)
    log_density = kde.score_samples(grid_coords_rad)
    density = np.exp(log_density)
    return {
        "grid_x": xx.tolist(), "grid_y": yy.tolist(),
        "density": density.reshape(xx.shape).tolist(),
        "center": [coords[:, 0].mean(), coords[:, 1].mean()],
        "poi_coords": coords.tolist()
    }


def generate_density_insight(pois, radius_m=600):
    """空间洞察生成（适配多POI类型）"""
    points = []
    valid_pois = []
    for poi in pois:
        try:
            location = poi.get("location", "")
            if not location or "," not in location:
                continue
            lng_str, lat_str = location.split(",", 1)
            lng = float(lng_str.strip())
            lat = float(lat_str.strip())
            points.append((lng, lat))
            valid_pois.append(poi)
        except:
            continue
    if len(points) < 2:
        return "暂无法分析空间分布特征。"
    densities = []
    for i in range(len(points)):
        count = 0
        for j in range(len(points)):
            dist = haversine(points[i][0], points[i][1], points[j][0], points[j][1])
            if dist <= radius_m:
                count += 1
        densities.append(count)
    avg_density = sum(densities) / len(densities)
    # 适配多类型描述
    poi_types = list(set([p.get('type', '目标设施').split(';')[0] for p in valid_pois]))
    type_desc = "、".join(poi_types[:3])
    if len(poi_types) > 3:
        type_desc += "等多类型设施"
    if avg_density >= 5:
        desc = "高度密集，商业氛围浓厚"
    elif avg_density >= 3:
        desc = "分布较密集，选择丰富"
    elif avg_density >= 2:
        desc = "分布较为稀疏，环境安静"
    else:
        desc = "极为稀疏，具有独占性"
    return f"🌐 空间洞察：该区域{type_desc}分布{desc}（平均 {avg_density:.1f} 家/{radius_m}m 半径）。"


# ========== 页面路由 ==========
@app.route('/')
def index():
    return render_template('index.html')


# ========== 【升级】适配多关键词的查询接口 ==========
@app.route('/search', methods=['POST'])
def search():
    data = request.get_json()
    user_query = data.get("query", "").strip()
    session_id = data.get("session_id", "default_session")

    if not user_query:
        return jsonify({"error": "请输入查询内容"}), 400

    # 1. 获取上下文城市
    last_city = DEFAULT_CITY
    if session_id in conversation_context:
        last_city = conversation_context[session_id].get("last_city", DEFAULT_CITY)

    # 2. 解析用户查询（支持多关键词）
    parsed = parse_query_with_context(user_query, last_city)
    city = parsed.get("city", DEFAULT_CITY).strip() or DEFAULT_CITY
    keywords = parsed.get("keywords", ["餐厅"])
    sub_keywords = parsed.get("sub_keywords", [])
    location_desc = parsed.get("location_desc", "").strip()

    # 3. 更新会话上下文
    conversation_context[session_id] = {
        "last_city": city,
        "last_keywords": keywords,
        "last_sub_keywords": sub_keywords,
        "last_location_desc": location_desc
    }

    # 4. 地理编码
    center = ""
    if location_desc:
        center = geocode_address(location_desc, city)

    # 5. 多关键词POI搜索
    pois, has_sub_flags = search_poi(city, keywords, sub_keywords, center)

    # 6. 空间分析
    kde_data = calculate_kde(pois)
    insight = generate_density_insight(pois, radius_m=1000)

    # 7. 格式化返回结果
    results = []
    for i, (poi, has_sub) in enumerate(zip(pois[:RESULT_LIMIT], has_sub_flags[:RESULT_LIMIT]), 1):
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

    print(f"[查询完成] 目标POI类型:{keywords}, 附属条件:{sub_keywords}, 返回结果数:{len(results)}")
    return jsonify({
        "query": user_query,
        "insight": insight,
        "kde": kde_data,
        "total": len(results),
        "results": results,
        "session_id": session_id,
        "keywords": keywords  # 返回给前端，用于展示
    })


# ========== 对话式查询接口 ==========
@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json()
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id", "default_session")

    if not user_message:
        return jsonify({"error": "请输入消息"}), 400

    response_data = process_chat_message(
        user_query=user_message,
        session_id=session_id,
        conversation_context=conversation_context,
        parse_func=parse_query_with_context,
        geocode_func=geocode_address,
        search_func=search_poi,
        kde_func=calculate_kde,
        insight_func=generate_density_insight,
    )
    return jsonify(response_data)


@app.route('/reset', methods=['POST'])
def reset():
    data = request.get_json()
    session_id = data.get("session_id", "")
    if session_id and session_id in conversation_context:
        del conversation_context[session_id]
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')

