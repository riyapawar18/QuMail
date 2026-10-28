// Central API configuration.
// - Production: FastAPI serves the compiled frontend, so relative URLs are same-origin.
// - Development: CRA's "proxy" in package.json forwards relative calls to the backend.
// - Override with REACT_APP_API_BASE (e.g. https://qumail-api.onrender.com).
const configured = (process.env.REACT_APP_API_BASE || "").trim();

export const API_BASE = configured.replace(/\/+$/, "");

const DEMO_TOKEN_KEY = "qumail_demo_token";

export const getDemoToken = () => {
  try {
    return sessionStorage.getItem(DEMO_TOKEN_KEY) || "";
  } catch {
    return "";
  }
};

export const setDemoToken = (token) => {
  try {
    sessionStorage.setItem(DEMO_TOKEN_KEY, token);
  } catch {
    /* storage may be unavailable (private mode) */
  }
};

export const clearDemoToken = () => {
  try {
    sessionStorage.removeItem(DEMO_TOKEN_KEY);
  } catch {
    /* ignore */
  }
};

// fetch() wrapper that attaches the guest session header when one exists.
export const apiFetch = (path, options = {}) => {
  const headers = new Headers(options.headers || {});
  const token = getDemoToken();
  if (token && !headers.has("X-Demo-Session")) {
    headers.set("X-Demo-Session", token);
  }
  return fetch(`${API_BASE}${path}`, { ...options, headers });
};

export const readJson = async (response) => {
  try {
    return await response.json();
  } catch {
    return {};
  }
};
