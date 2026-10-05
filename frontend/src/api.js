const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

async function request(path, options = {}) {
  const res = await fetch(`${BASE}/api/${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data?.text?.[0] || data?.detail || "تعذّر الاتصال بالخادم. تحقّق من اتصالك ثم أعد المحاولة.";
    throw new Error(msg);
  }
  return data;
}

export const verify = (text) =>
  request("verify", { method: "POST", body: JSON.stringify({ text, extract: true }) });

export const reportError = (payload) =>
  request("reports", { method: "POST", body: JSON.stringify(payload) });

export const getSources = () => request("sources");

export const getSpecialist = (textId) => request(`specialist/${textId}`);
