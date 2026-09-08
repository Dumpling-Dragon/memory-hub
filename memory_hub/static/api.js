// Only keep an optional access token for the lifetime of this browser tab.
let tokenQuestion = null;
async function apiFetch(url, options = {}) {
  const send = () => {
    const headers = new Headers(options.headers || {});
    const token = sessionStorage.getItem('mh-token');
    if (token) headers.set('Authorization', `Bearer ${token}`);
    return fetch(url, {...options, headers});
  };
  let response = await send();
  if (response.status === 401) {
    if (!tokenQuestion) {
      tokenQuestion = Promise.resolve().then(() => {
        const token = window.prompt('请输入 Memory Hub 访问口令（仅在本标签页保存）');
        if (token) sessionStorage.setItem('mh-token', token);
        return !!token;
      });
    }
    const accepted = await tokenQuestion;
    tokenQuestion = null;
    if (accepted) response = await send();
  }
  let data;
  try { data = await response.json(); } catch { throw new Error(`服务返回异常 (${response.status})`); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`);
  return data;
}
