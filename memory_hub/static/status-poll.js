// One in-flight request per page, with a ten-second pause after completion.
window.pollMemoryStatus = function (onData, onError) {
  let timer;
  let active = false;
  async function tick() {
    clearTimeout(timer);
    if (active || document.hidden) return;
    active = true;
    try {
      onData(await apiFetch('/api/status', { cache: 'no-store' }));
    } catch (error) {
      if (onError) onError(error);
    } finally {
      active = false;
      if (!document.hidden) timer = setTimeout(tick, 10000);
    }
  }
  document.addEventListener('visibilitychange', tick);
  window.addEventListener('pagehide', () => clearTimeout(timer));
  window.addEventListener('pageshow', tick);
  tick();
};
