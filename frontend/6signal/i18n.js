(() => {
  'use strict';

  const replacements = new Map([
    ['GLOBAL MARKET INTELLIGENCE', '全球市场情报'],
    ['LIVE DATA', '实时数据'],
    ['DATA HEALTHY', '数据健康'],
    ['DATA HEALTH', '数据健康'],
    ['HEALTHY', '健康'],
    ['DEGRADED', '降级'],
    ['ISSUE', '异常'],
    ['Signal Intelligence', '信号智能'],
    ['Global Sentiment', '全球市场情绪'],
    ['Global Risk / Market Regime', '全球风险 / 市场状态'],
    ['Market Temperature', '市场温度'],
    ['Data Coverage', '数据覆盖'],
    ['Risk Appetite', '风险偏好'],
    ['Risk-Off', '风险规避'],
    ['Risk-On', '风险偏好'],
    ['Risk-On Score', '风险偏好评分'],
    ['Risk Level', '风险等级'],
    ['SENTIMENT', '情绪'],
    ['INSUFFICIENT DATA', '数据不足'],
    ['Neutral', '中性'],
    ['NEUTRAL', '中性'],
    ['Crypto Derivatives', '加密衍生品'],
    ['Crypto', '加密资产'],
    ['Theme Leadership', '主题领涨'],
    ['SOURCE MONITOR', '来源监控'],
    ['Data Health', '数据健康'],
    ['BINANCE SNAPSHOT', 'Binance 快照'],
    ['STRUCTURED', '结构化'],
    ['confidence', '置信度'],
    ['RULES_FALLBACK', '规则引擎回退'],
    ['RULES', '规则引擎'],
    ['rules', '规则引擎'],
    ['high', '高'],
    ['medium', '中'],
    ['low', '低'],
    ['score', '评分'],
    ['VIX level/change + deterministic market-regime penalty', 'VIX 水平/变动 + 确定性市场状态惩罚'],
    ['signals', '信号'],
    ['alerts', '警报'],
    ['real mapping', '真实映射'],
    ['event-time snapshot', '事件时间快照'],
    ['last event', '最近事件'],
    ['latency', '延迟'],
    ['status based on event time', '状态基于事件时间'],
    ['Funding', '资金费率'],
    ['Basis', '基差'],
    ['Open Interest', '未平仓量'],
    ['Liquidation 24H', '24 小时清算'],
    ['Spread', '价差'],
    ['Event age', '事件延迟'],
    ['Price', '价格'],
    ['Status', '状态'],
    ['Macro', '宏观'],
    ['themes', '主题'],
    ['Commodities', '商品'],
    ['US Tech', '美国科技'],
    ['China Assets', '中国资产'],
    ['Safe Haven', '避险资产'],
    ['macro_policy', '宏观政策'],
    ['china_policy', '中国政策'],
    ['energy', '能源'],
    ['crypto', '加密资产'],
    ['geopolitics', '地缘政治'],
    ['market_news', '市场新闻'],
    ['global_market', '全球市场'],
    ['binance_spot', 'Binance 现货'],
    ['binance_futures', 'Binance 合约'],
    ['open_interest', '未平仓量'],
    ['liquidations', '清算'],
    ['Treasury', '美债'],
    ['Nasdaq', '纳斯达克'],
    ['S&P500', '标普500'],
    ['Gold', '黄金'],
    ['Oil', '原油'],
    ['Equities', '股票'],
    ['CBOE Volatility Index', 'CBOE 波动率指数'],
    ['Score', '评分'],
    ['风险偏好 Score', '风险偏好评分'],
    ['Equity', '股票'],
    ['USD', '美元'],
    ['Yield', '收益率'],
    ['News', '新闻'],
    ['Hysteria', '市场亢奋度'],
    ['LIVE', '实时'],
    ['DELAYED', '延迟'],
    ['STALE', '过期'],
    ['ERROR', '错误'],
    ['EVENT', '事件'],
    ['cross-asset', '跨资产'],
    ['OK', '正常'],
  ]);

  const tips = {
    '.brand-sub': '将行情、新闻、波动率和跨资产关系合并为可追溯的市场判断。',
    '.ticker-live': '由后端数据源返回的最新事件时间行情；页面不会用随机数补齐缺失行情。',
    '#strengthTabs [data-strength="crypto"]': '加密资产现货、永续合约及其衍生品指标的集合。',
    '.market-bottom .mini-panel:first-child .sub-title': '把市场状态、波动率、美元和利率等因子压缩成 0–100 的风险偏好温度。',
    '.market-bottom .stat-label': '风险偏好表示市场更偏好风险资产还是更偏好避险资产，不等于买卖建议。',
    '.market-bottom .mini-panel:nth-child(2) .sub-title': '当前预期应有多少核心市场序列已经被有效采集；覆盖率低时结论置信度会下降。',
    '.derivatives-card .sub-title': '观察资金费率、现货/合约基差、未平仓量、清算和盘口价差。',
    '.derivatives-card .panel-meta': '以事件发生时间作为新鲜度判断，而不是以后端收到数据的时间替代。',
    '.signals-panel .panel-title': '由规则和跨资产确认生成的机会、风险与异常信号集合；它不是自动下单指令。',
    '.signals-panel .badge-pro': '结构化输出：后端可选地返回固定字段，而不是让模型直接修改量化分数。',
    '.global-sentiment .panel-title': '将规则计算出的市场状态转换为偏多、中性、偏空的整体分布，用于快速判断情绪方向。',
    '.risk-panel .panel-title': '全球风险是风险评分；市场状态是由多个市场因子推导出的状态标签，两者共同描述当前风险环境。',
    '.concept-panel .panel-title': '把新闻关键词映射到资产主题，再用主题内资产的平均变动估计相对强弱。',
    '#sentimentExplain': '查看评分来源和当前数据覆盖情况。',
  };

  function localizeText(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      if (!node.nodeValue || !node.nodeValue.trim()) return;
      let value = node.nodeValue;
      replacements.forEach((to, from) => { value = value.split(from).join(to); });
      if (value !== node.nodeValue) node.nodeValue = value;
    });
  }

  function applyTips() {
    Object.entries(tips).forEach(([selector, text]) => {
      document.querySelectorAll(selector).forEach(el => {
        el.classList.add('term');
        el.setAttribute('tabindex', el.getAttribute('tabindex') || '0');
        el.dataset.tip = text;
      });
    });
  }

  function run() {
    localizeText(document.body);
    applyTips();
  }

  const observer = new MutationObserver(() => requestAnimationFrame(run));
  observer.observe(document.body, { childList: true, subtree: true });
  run();
})();
