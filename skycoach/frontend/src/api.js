const TOKEN_KEY = 'skycoach.token'

export const auth = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

async function request(path, { method = 'GET', body, isForm = false } = {}) {
  const headers = {}
  if (!isForm && body) headers['Content-Type'] = 'application/json'
  const token = auth.get()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(path, {
    method,
    headers,
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  })

  if (res.status === 204) return null
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`)
  return data
}

export const api = {
  register: (email, password, name) =>
    request('/api/auth/register', { method: 'POST', body: { email, password, name } }),
  login: (email, password) =>
    request('/api/auth/login', { method: 'POST', body: { email, password } }),
  me: () => request('/api/me'),
  updateMe: (patch) => request('/api/me', { method: 'PATCH', body: patch }),

  analyze: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('/api/analyze', { method: 'POST', body: fd, isForm: true })
  },
  uploadFlight: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('/api/flights', { method: 'POST', body: fd, isForm: true })
  },
  listFlights: () => request('/api/flights'),
  getFlight: (id) => request(`/api/flights/${id}`),
  deleteFlight: (id) => request(`/api/flights/${id}`, { method: 'DELETE' }),

  checkout: (success_url, cancel_url) =>
    request('/api/billing/checkout', { method: 'POST', body: { success_url, cancel_url } }),
}
