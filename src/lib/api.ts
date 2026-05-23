import axios from 'axios'

const GAMMA_API = import.meta.env.VITE_POLYMARKET_API_URL || 'https://gamma-api.polymarket.com'

export async function fetchActiveTemperatureMarkets() {
  const res = await axios.get(`${GAMMA_API}/markets`, {
    params: { tag_id: 12, active: true, closed: false, limit: 100 },
  })
  return res.data
}
