// 字号自适应:检测用户放大屏幕(捏合/浏览器缩放)后停留阅读的倍数并记忆,
// 下次访问直接以该字号布局,响应式重排保证整屏显示,无需再手动放大。
// 误判时可调用 fbResetFontScale() 恢复默认。
(function () {
  var KEY = 'fb_font_scale', DPR_KEY = 'fb_base_dpr';
  try {
    var baseDpr = parseFloat(localStorage.getItem(DPR_KEY));
    if (!(baseDpr > 0)) { baseDpr = window.devicePixelRatio || 1; localStorage.setItem(DPR_KEY, String(baseDpr)); }
    var saved = parseFloat(localStorage.getItem(KEY));
    if (!(saved > 0)) saved = 1;
    saved = Math.min(2.5, Math.max(1, saved));

    var vv = window.visualViewport;
    function browserZoom() { return (window.devicePixelRatio || 1) / baseDpr; }
    function pinch() { return vv && vv.scale > 1 ? vv.scale : 1; }
    function readingScale() { return Math.round(browserZoom() * pinch() * 100) / 100; }

    function apply() {
      // 补偿当前浏览器缩放,保证最终视觉字号稳定为 saved
      var z = Math.min(2.5, Math.max(1, saved / browserZoom()));
      document.body.style.zoom = (z > 1.04) ? z : '';
      document.documentElement.classList.toggle('fb-large', z >= 1.25);
    }
    apply();

    var timer = null;
    function onZoomChange() {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var c = readingScale();
        if (c >= 0.8 && Math.abs(c - saved) >= 0.05) {
          saved = Math.max(1, c);
          localStorage.setItem(KEY, String(saved));
          apply();
        }
      }, 3000);
    }
    if (vv) vv.addEventListener('resize', onZoomChange);
    window.addEventListener('resize', onZoomChange);

    window.fbResetFontScale = function () {
      saved = 1;
      try { localStorage.setItem(KEY, '1'); } catch (e) {}
      apply();
    };
  } catch (e) {}
})();
