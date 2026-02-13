import json
import csv
import os
import requests
import re
from dashscope import Generation
import dashscope

# ===== 配置你的 API Keys =====
DASHSCOPE_API_KEY = "sk-9c5f6447cd3b48deb6ee603141759be6"  # 替换为你的 Qwen Key
AMAP_API_KEY = "3e1379e3c44e93990c9c31dd707b031e"          # 替换为你的高德 Key
dashscope.api_key = DASHSCOPE_API_KEY

def parse_query(user_query):
    """从用户查询中提取 city 和 keyword"""
    prompt = f"""
你是一个地理信息解析助手，请严格按照以下格式输出 JSON：

规则：
1. 必须输出纯 JSON，不要任何解释、标题、换行
2. 字段必须完整：city, keyword, location_desc
3. 如果没有，则设为空字符串（""）
4. city 是城市名（如 北京、上海）
5. keyword 是POI类型关键词（如 咖啡店、医院、加油站）
6. location_desc 是具体地点（如 西湖、科技园、附近），若无则为空

示例：
输入: "杭州西湖的咖啡店"
输出: {{"city": "杭州", "keyword": "咖啡店", "location_desc": "西湖"}}

输入: "北京中关村附近的奶茶店"
输出: {{"city": "北京", "keyword": "奶茶店", "location_desc": "中关村附近"}}

输入: "附近有药店吗？"
输出: {{"city": "", "keyword": "药店", "location_desc": "附近"}}

现在处理:
用户查询: "{user_query}"
输出:
"""
    try:
        response = Generation.call(model="qwen-plus", prompt=prompt, result_format="message")
        raw = response.output.choices[0].message.content.strip()
        json_str = re.search(r"\{.*\}", raw, re.DOTALL).group(0)
        return json.loads(json_str)
    except Exception as e:
        print(f"解析失败: {e}")
        return {"city": "", "keyword": "", "location_desc": ""}

def geocode(city, addr):
    if not addr or not city:
        return ""
    url = "https://restapi.amap.com/v3/geocode/geo"
    params = {"key": AMAP_API_KEY, "address": addr, "city": city}
    try:
        r = requests.get(url, params=params, timeout=5).json()
        return r["geocodes"][0]["location"] if r["status"] == "1" and r["geocodes"] else ""
    except:
        return ""

def search_poi(city, keyword, center=""):
    """搜索 POI，最多返回 15 条"""
    url = "https://restapi.amap.com/v3/place/around" if center else "https://restapi.amap.com/v3/place/text"
    params = {
        "key": AMAP_API_KEY,
        "keywords": keyword,
        "offset": 15,  # 请求15条
        "extensions": "all"
    }
    if center:
        params.update({"location": center, "radius": 2000})
    else:
        params["city"] = city

    try:
        r = requests.get(url, params=params, timeout=5).json()
        return r.get("pois", []) if r["status"] == "1" else []
    except:
        return []

def keyword_in_name(poi_name, keyword):
    """判断 POI 名称是否与 keyword 语义相关"""
    if not keyword or not poi_name:
        return False

    # 同义词映射表（可按需扩展）
    synonym_map = {
        "咖啡": ["咖啡", "coffee", "cafe", "Cafe", "COFFEE", "星巴克", "瑞幸", "Manner", "Seesaw"],
        "奶茶": ["奶茶", "茶饮", "喜茶", "CoCo", "一点点", "奈雪", "茶颜悦色", "茶"],
        "火锅": ["火锅", "火鍋", "麻辣烫", "串串香", "冒菜", "重庆火锅"],
        "医院": ["医院", "诊所", "卫生院", "医疗中心", "体检中心"],
        "加油站": ["加油站", "加气站", "中石化", "中石油", "壳牌", "BP"],
        "停车场": ["停车", "停车场", "车位", "停靠", "地下车库"],
        "民宿": ["民宿", "公寓", "短租", "loft", "居", "住", "客栈","酒店","快捷酒店","青年旅馆"],
        "电影院": ["影院", "电影", "IMAX", "CGV", "UME", "万达影城", "百老汇"],
        "药店": ["药房", "药店", "大药房", "同仁堂", "老百姓"],
        "超市": ["超市", "便利店", "沃尔玛", "家乐福", "永辉", "7-Eleven"],
        "餐厅":["小吃店","餐馆","饭店"]
    }

    # 1. 直接包含
    if keyword in poi_name:
        return True

    # 2. 检查是否属于某个语义类别
    for main_word, synonyms in synonym_map.items():
        if main_word in keyword or keyword in main_word:
            for syn in synonyms:
                if syn in poi_name:
                    return True
    return False

def run_evaluation():
    # 确保 test_cases.json 存在
    if not os.path.exists('test_cases.json'):
        print("❌ 错误：未找到 test_cases.json，请先创建测试集！")
        return

    with open('test_cases.json', 'r', encoding='utf-8') as f:
        test_cases = json.load(f)

    # 如果结果文件存在，先删除（避免权限错误）
    output_file = 'evaluation_results.csv'
    if os.path.exists(output_file):
        os.remove(output_file)

    results = []
    print("🚀 开始评估...")

    for i, case in enumerate(test_cases, 1):
        query = case["query"]
        ground_city = case.get("city", "")  # 用于兜底

        print(f"\n[{i}/{len(test_cases)}] 测试: {query}")

        # 1. 解析 query
        params = parse_query(query)
        extracted_city = params.get("city", "").strip()
        keyword = params.get("keyword", "").strip()
        loc_desc = params.get("location_desc", "").strip()

        # 城市兜底
        if not extracted_city:
            extracted_city = ground_city

        if not extracted_city or not keyword:
            print("  ❌ 解析失败：未提取到城市或关键词")
            results.append({
                "query": query,
                "success": False,
                "keyword_used": keyword,
                "matched_poi": "",
                "returned_count": 0
            })
            continue

        # 2. 获取坐标（如果需要）
        center = geocode(extracted_city, loc_desc) if loc_desc else ""

        # 3. 搜索 POI（最多15条）
        pois = search_poi(extracted_city, keyword, center)
        returned_names = [p["name"] for p in pois[:15]]

        # 4. 判断是否成功：只要有一条匹配 keyword 即可
        success = False
        matched_poi = ""
        for name in returned_names:
            if keyword_in_name(name, keyword):
                success = True
                matched_poi = name
                break

        print(f"  🧠 提取: city='{extracted_city}', keyword='{keyword}'")
        print(f"  ✅ 返回数量: {len(returned_names)}")
        if success:
            print(f"  🎯 匹配成功: '{matched_poi}'")
        else:
            print(f"  ❌ 无匹配结果")

        results.append({
            "query": query,
            "success": success,
            "keyword_used": keyword,
            "matched_poi": matched_poi,
            "returned_count": len(returned_names)
        })

    # 计算成功率
    total = len(results)
    success_count = sum(1 for r in results if r["success"])
    success_rate = success_count / total if total > 0 else 0

    # 保存结果
    with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=["query", "success", "keyword_used", "matched_poi", "returned_count"])
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    # 打印总结
    print("\n" + "="*60)
    print("📊 评估完成！")
    print(f"✅ 任务成功率: {success_rate:.2%} ({success_count}/{total})")
    print(f"📁 详细结果已保存至: {output_file}")
    print("="*60)

if __name__ == '__main__':
    run_evaluation()