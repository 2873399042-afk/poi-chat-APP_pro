// ========== 全局变量 ==========
let map = null;
let typeChart = echarts.init(document.getElementById('typeChart'));
let markers = [];
let currentPoiCoords = [];
let sessionId = 'session_' + Math.random().toString(36).substr(2, 9);

// ========== 页面加载完成初始化 ==========
window.onload = function() {
    initMap(); // 初始化带底图的地图
    bindEvents(); // 绑定查询事件
};

// ========== 【核心修改】初始化带默认底图的地图 ==========
function initMap() {
    map = new AMap.Map('map', {
        zoom: 13,
        center: [118.89, 32.12], // 南京默认中心
        viewMode: '3D', // 保留3D视角
        pitch: 10,
        // 【关键】添加默认标准街道底图，解决空白问题
        layers: [
            new AMap.createDefaultLayer({
                style: 'amap://styles/light', // 浅色标准底图，可选：dark(暗色)/normal(标准)
                zIndex: 1
            })
        ],
        resizeEnable: true // 自适应窗口大小
    });
}

// ========== 绑定所有事件 ==========
function bindEvents() {
    // 查询按钮点击
    document.getElementById('searchBtn').addEventListener('click', doSearch);
    // 回车查询
    document.getElementById('queryInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') doSearch();
    });
    // 窗口大小变化时自适应图表
    window.addEventListener('resize', () => typeChart.resize());
}

// ========== 核心查询函数（完全保留你的多POI查询功能） ==========
async function doSearch() {
    const query = document.getElementById('queryInput').value.trim();
    if (!query) return;

    // 清空旧数据
    clearMapMarkers();
    document.getElementById('poiList').innerHTML = '';
    document.getElementById('insightText').textContent = '正在分析中...';
    document.getElementById('totalCount').textContent = '0';
    currentPoiCoords = [];

    try {
        // 调用后端接口
        const response = await fetch('/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, session_id: sessionId })
        });
        const data = await response.json();
        if (data.error) {
            alert(data.error);
            return;
        }

        // 保存POI坐标
        currentPoiCoords = data.results.map(poi => [parseFloat(poi.lng), parseFloat(poi.lat)]);
        // 渲染所有内容
        renderInsight(data.insight);
        renderPoiList(data.results);
        renderMapMarkers(data.results);
        renderTypeChart(data.results);
        document.getElementById('totalCount').textContent = data.total;

    } catch (error) {
        console.error('查询失败', error);
        alert('查询失败，请检查后端服务是否正常运行');
    }
}

// ========== 渲染空间洞察 ==========
function renderInsight(insight) {
    document.getElementById('insightText').textContent = insight;
}

// ========== 渲染POI列表 ==========
function renderPoiList(pois) {
    const list = document.getElementById('poiList');
    pois.forEach(poi => {
        const item = document.createElement('li');
        item.className = 'poi-item';
        item.innerHTML = `
            <div class="name">${poi.name}</div>
            <div class="address">${poi.address}</div>
            <div class="coords"> 经纬度: ${poi.lng}, ${poi.lat}</div>
            ${poi.has_sub_poi ? '<span class="tag">✅ 满足附属条件</span>' : ''}
        `;
        // 点击列表项，地图跳转到对应POI
        item.addEventListener('click', () => {
            map.setZoomAndCenter(15, [parseFloat(poi.lng), parseFloat(poi.lat)]);
        });
        list.appendChild(item);
    });
}

// ========== 渲染地图POI标记 ==========
function renderMapMarkers(pois) {
    if (pois.length === 0) return;
    // 调整地图中心到第一个POI
    const firstPoi = pois[0];
    map.setZoomAndCenter(13, [parseFloat(firstPoi.lng), parseFloat(firstPoi.lat)]);

    // 批量添加标记
    pois.forEach(poi => {
        const lng = parseFloat(poi.lng);
        const lat = parseFloat(poi.lat);
        if (!lng || !lat) return;

        // 创建蓝色标记
        const marker = new AMap.Marker({
            position: [lng, lat],
            title: poi.name,
            icon: 'https://webapi.amap.com/theme/v1.3/markers/n/mark_b.png'
        });
        marker.setMap(map);
        markers.push(marker);

        // 点击标记显示信息弹窗
        marker.on('click', () => {
            new AMap.InfoWindow({
                content: `<h4 style="margin-bottom:5px;">${poi.name}</h4><p style="margin:0;color:#666;">${poi.address}</p>`,
                offset: new AMap.Pixel(0, -30)
            }).open(map, [lng, lat]);
        });
    });
}

// ========== 渲染POI类型分布图表 ==========
function renderTypeChart(pois) {
    // 统计POI一级类型数量
    const typeCount = {};
    pois.forEach(poi => {
        const firstType = poi.type.split(';')[0] || '未知类型';
        typeCount[firstType] = (typeCount[firstType] || 0) + 1;
    });

    // 转换为ECharts格式
    const chartData = Object.entries(typeCount).map(([name, value]) => ({ name, value }));

    // 图表配置
    const option = {
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: {
            type: 'category',
            data: chartData.map(d => d.name),
            axisLabel: { rotate: 30 }
        },
        yAxis: { type: 'value' },
        series: [{
            type: 'bar',
            data: chartData.map(d => d.value),
            itemStyle: { color: '#409eff' },
            barWidth: '40%'
        }]
    };

    // 渲染图表
    typeChart.setOption(option, true);
}

// ========== 清空地图标记 ==========
function clearMapMarkers() {
    markers.forEach(marker => marker.setMap(null));
    markers = [];
}
