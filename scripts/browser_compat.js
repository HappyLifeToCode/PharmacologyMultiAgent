// 反自动化检测注入脚本（playwright-mcp --init-script）
// 在每个页面的任何脚本执行前注入，抹除 headless/自动化的常见指纹特征。
// 与 playwright_mcp_config.json 的 UA（Chrome/124, Windows）保持一致，改 UA 要同步改这里。

// 1. navigator.webdriver：自动化最直接的标志
Object.defineProperty(navigator, 'webdriver', {
  get: () => undefined,
});

// 2. 语言列表：headless 默认可能为空或不全
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-US', 'en'],
});

// 3. 插件数量：headless 下 plugins.length 常为 0，真实浏览器不为 0
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5],
});

// 4. 硬件并发数/内存：headless 常暴露异常值
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

// 5. window.chrome：真实 Chrome 存在，headless 下缺失
if (!window.chrome) {
  window.chrome = { runtime: {} };
}

// 6. WebGL 渲染器：headless 默认报 SwiftShader（软件渲染），是强 headless 信号。
//    伪装成常见的 Intel ANGLE 硬件渲染。
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (parameter) {
  // UNMASKED_VENDOR_WEBGL
  if (parameter === 37445) return 'Google Inc. (Intel)';
  // UNMASKED_RENDERER_WEBGL
  if (parameter === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)';
  return getParameter.call(this, parameter);
};

// 7. permissions.query 对 notification 的处理：自动化环境下该查询会抛异常，
//    真实浏览器返回 denied。这也是常见检测点。
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) =>
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters);
}
