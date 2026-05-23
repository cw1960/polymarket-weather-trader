// City → (ICAO, country_code) mapping for Wunderground's api.weather.com
// historical observations endpoint. Mirrors scripts/wunderground.py STATIONS.
// Cities not in this table either don't resolve from Wunderground (per
// Polymarket rule text) or had no series available at extraction time.

export const WU_STATIONS: Record<string, { icao: string; cc: string }> = {
  "NYC":           { icao: "KLGA", cc: "US" },
  "Chicago":       { icao: "KORD", cc: "US" },
  "Miami":         { icao: "KMIA", cc: "US" },
  "Los Angeles":   { icao: "KLAX", cc: "US" },
  "Dallas":        { icao: "KDAL", cc: "US" },
  "Atlanta":       { icao: "KATL", cc: "US" },
  "Houston":       { icao: "KHOU", cc: "US" },
  "Austin":        { icao: "KAUS", cc: "US" },
  "Seattle":       { icao: "KSEA", cc: "US" },
  "San Francisco": { icao: "KSFO", cc: "US" },
  "Denver":        { icao: "KBKF", cc: "US" },
  "London":        { icao: "EGLC", cc: "GB" },
  "Paris":         { icao: "LFPB", cc: "FR" },
  "Madrid":        { icao: "LEMD", cc: "ES" },
  "Munich":        { icao: "EDDM", cc: "DE" },
  "Milan":         { icao: "LIMC", cc: "IT" },
  "Amsterdam":     { icao: "EHAM", cc: "NL" },
  "Warsaw":        { icao: "EPWA", cc: "PL" },
  "Helsinki":      { icao: "EFHK", cc: "FI" },
  "Ankara":        { icao: "LTAC", cc: "TR" },
  "Jeddah":        { icao: "OEJN", cc: "SA" },
  "Seoul":         { icao: "RKSI", cc: "KR" },
  "Tokyo":         { icao: "RJTT", cc: "JP" },
  "Busan":         { icao: "RKPK", cc: "KR" },
  "Taipei":        { icao: "RCSS", cc: "TW" },
  "Beijing":       { icao: "ZBAA", cc: "CN" },
  "Shanghai":      { icao: "ZSPD", cc: "CN" },
  "Guangzhou":     { icao: "ZGGG", cc: "CN" },
  "Shenzhen":      { icao: "ZGSZ", cc: "CN" },
  "Chengdu":       { icao: "ZUUU", cc: "CN" },
  "Chongqing":     { icao: "ZUCK", cc: "CN" },
  "Wuhan":         { icao: "ZHHH", cc: "CN" },
  "Singapore":     { icao: "WSSS", cc: "SG" },
  "Kuala Lumpur":  { icao: "WMKK", cc: "MY" },
  "Manila":        { icao: "RPLL", cc: "PH" },
  "Jakarta":       { icao: "WIHH", cc: "ID" },
  "Lucknow":       { icao: "VILK", cc: "IN" },
  "Karachi":       { icao: "OPKC", cc: "PK" },
  "Wellington":    { icao: "NZWN", cc: "NZ" },
  "Toronto":       { icao: "CYYZ", cc: "CA" },
  "Mexico City":   { icao: "MMMX", cc: "MX" },
  "Buenos Aires":  { icao: "SAEZ", cc: "AR" },
  "Cape Town":     { icao: "FACT", cc: "ZA" },
  "Lagos":         { icao: "DNMM", cc: "NG" },
}

// Public API key used by wunderground.com itself. Same key the bot uses.
export const WU_APIKEY = "e1f10a1e78da46f5b10a1e78da96f525"
