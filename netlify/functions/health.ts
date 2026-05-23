import { createClient } from '@supabase/supabase-js'

export const handler = async () => {
  const sb = createClient(
    process.env.VITE_SUPABASE_URL!,
    process.env.VITE_SUPABASE_ANON_KEY!
  )
  const { count } = await sb
    .from('trade_signals')
    .select('*', { count: 'exact', head: true })

  return {
    statusCode: 200,
    body: JSON.stringify({ signal_count: count ?? 0, status: 'ok' }),
  }
}
