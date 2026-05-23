-- ============================================================
-- Seed resolution_stations for all 50 Polymarket cities
-- Run in Supabase SQL Editor AFTER schema.sql
--
-- Station IDs are NOAA GHCN Daily format.
-- Confidence: HIGH = verified in GHCN with TMAX data;
--             MEDIUM = proxy/nearby station;
--             NO_TMAX = station exists but GHCN has no TMAX; delta=0
--
-- US cities resolve in °F. All others in °C.
-- ============================================================

insert into resolution_stations
  (city, station_id, station_name, source, lat, lon, unit, polymarket_slug)
values

-- ── US CITIES (°F) ──────────────────────────────────────────
-- Confidence: HIGH (NOAA GHCN direct)
('NYC',           'USW00014732', 'LaGuardia Airport',                    'NOAA', 40.7772, -73.8726, 'F', 'highest-temp-nyc'),
('Chicago',       'USW00094846', 'Chicago OHare Intl Airport',           'NOAA', 41.9803, -87.9090, 'F', 'highest-temp-chicago'),
('Miami',         'USW00012839', 'Miami International Airport',          'NOAA', 25.7959, -80.2870, 'F', 'highest-temp-miami'),
('Los Angeles',   'USW00023174', 'Los Angeles International Airport',    'NOAA', 33.9425, -118.4081,'F', 'highest-temp-los-angeles'),
('Dallas',        'USW00013960', 'Dallas Love Field',                    'NOAA', 32.8481, -96.8511, 'F', 'highest-temp-dallas'),
('Atlanta',       'USW00013874', 'Hartsfield-Jackson International',     'NOAA', 33.6407, -84.4277, 'F', 'highest-temp-atlanta'),
('Houston',       'USW00012919', 'William P. Hobby Airport',             'NOAA', 29.6454, -95.2789, 'F', 'highest-temp-houston'),
('Austin',        'USW00013904', 'Austin-Bergstrom International',       'NOAA', 30.1945, -97.6699, 'F', 'highest-temp-austin'),
('Seattle',       'USW00024233', 'Seattle-Tacoma International Airport', 'NOAA', 47.4444, -122.3138,'F', 'highest-temp-seattle'),
('San Francisco', 'USW00023234', 'San Francisco International Airport',  'NOAA', 37.6213, -122.3790,'F', 'highest-temp-san-francisco'),
-- Confidence: VERIFY (Buckley SFB is military — may need nearby DEN station)
('Denver',        'USW00003017', 'Buckley Space Force Base',             'NOAA', 39.7169, -104.7516,'F', 'highest-temp-denver'),

-- ── EUROPE (°C) ─────────────────────────────────────────────
-- Confidence: HIGH
('London',        'UKM00003772', 'London Heathrow Airport',              'NOAA', 51.4780,  -0.4610, 'C', 'highest-temp-london'),
-- Note: Paris Le Bourget (FRM00007150) has no TMAX data in GHCN; using Orly.
('Paris',         'FRM00007149', 'Paris-Orly Airport',                   'NOAA', 48.7167,   2.3842, 'C', 'highest-temp-paris'),
-- Note: SPM00008221 not in GHCN; using Barajas SPE station.
('Madrid',        'SPE00120278', 'Madrid/Barajas Airport',               'NOAA', 40.4667,  -3.5556, 'C', 'highest-temp-madrid'),
-- Note: GMM00010866 not in GHCN; Schwaigermoos is 2km from MUC airport.
('Munich',        'GMM00010870', 'Schwaigermoos (nr Munich Airport)',    'NOAA', 48.3667,  11.8000, 'C', 'highest-temp-munich'),
-- Note: ITM00016080 not in GHCN; Cameri is 12km from Malpensa.
('Milan',         'ITM00016064', 'Cameri (nr Milan Malpensa)',           'NOAA', 45.5300,   8.6690, 'C', 'highest-temp-milan'),
-- Note: NLM00006240 not in GHCN; using Schiphol NLE station.
('Amsterdam',     'NLE00152485', 'Amsterdam Airport Schiphol',           'NOAA', 52.3156,   4.7903, 'C', 'highest-temp-amsterdam'),
('Warsaw',        'PLM00012375', 'Warsaw Chopin Airport (Okecie)',       'NOAA', 52.1660,  20.9670, 'C', 'highest-temp-warsaw'),
-- Note: FIM00002978 not in GHCN; using Vantaa FIE station.
('Helsinki',      'FIE00142080', 'Helsinki-Vantaa Airport',              'NOAA', 60.3269,  24.9603, 'C', 'highest-temp-helsinki'),
-- Note: New Istanbul Airport (LTFM) has no GHCN station. Bolge Kartal is closest.
('Istanbul',      'TUM00017064', 'Istanbul Bolge Kartal (proxy)',        'NOAA', 41.2608,  28.7418, 'C', 'highest-temp-istanbul'),
-- Note: TRM00017130 was wrong prefix; correct is TUM00017130.
('Ankara',        'TUM00017130', 'Ankara/Central',                       'NOAA', 39.9500,  32.8830, 'C', 'highest-temp-ankara'),

-- ── RUSSIA (°C) ─────────────────────────────────────────────
-- Confidence: MEDIUM (Russia stopped sharing NOAA data post-2022; delta uses 2019-2021)
('Moscow',        'RSM00027612', 'Moscow GSN',                           'NOAA', 55.8331,  37.6167, 'C', 'highest-temp-moscow'),

-- ── MIDDLE EAST (°C) ────────────────────────────────────────
-- Confidence: HIGH
('Tel Aviv',      'ISM00040180', 'Ben Gurion International Airport',     'NOAA', 31.9964,  34.8963, 'C', 'highest-temp-tel-aviv'),
-- Note: SAM00041024 was wrong prefix; correct is SA000041024.
('Jeddah',        'SA000041024', 'Jeddah King Abdulaziz Intl',          'NOAA', 21.7000,  39.1830, 'C', 'highest-temp-jeddah'),

-- ── EAST ASIA (°C) ──────────────────────────────────────────
-- Note: HKM00045011 not in GHCN; Macau Intl is nearest with TMAX (~55km proxy).
('Hong Kong',     'MCM00045011', 'Macau International Airport (proxy)', 'NOAA', 22.1500, 113.5920, 'C', 'highest-temp-hong-kong'),
-- Note: KSM prefix wrong; correct is KS0. Korean stations have TAVG/TMIN only; delta=0.
('Seoul',         'KS000047112', 'Incheon International Airport',        'NOAA', 37.4670, 126.6330, 'C', 'highest-temp-seoul'),
-- Note: JAM prefix wrong; correct is JA0. Japanese stations have TAVG/TMIN only; delta=0.
('Tokyo',         'JA000047662', 'Tokyo (Haneda area)',                  'NOAA', 35.6830, 139.7670, 'C', 'highest-temp-tokyo'),
-- Note: KSM00047158 not in GHCN; 47159 is correct. Korean stations: delta=0.
('Busan',         'KSM00047159', 'Busan',                                'NOAA', 35.1000, 129.0330, 'C', 'highest-temp-busan'),
-- Note: Taiwan not in NOAA GHCN; delta=0.
('Taipei',        'TWM00046692', 'Taipei Songshan Airport',              'NOAA', 25.0697, 121.5500, 'C', 'highest-temp-taipei'),
-- Confidence: MEDIUM (Chinese stations — WMO→GHCN mapping)
('Beijing',       'CHM00054511', 'Beijing Capital International Airport','NOAA', 40.0799, 116.5847, 'C', 'highest-temp-beijing'),
('Shanghai',      'CHM00058362', 'Shanghai Pudong International Airport','NOAA', 31.1436, 121.8083, 'C', 'highest-temp-shanghai'),
('Guangzhou',     'CHM00059287', 'Guangzhou Baiyun International Airport','NOAA',23.3924, 113.2990, 'C', 'highest-temp-guangzhou'),
('Shenzhen',      'CHM00059501', 'Shenzhen Bao''an International Airport','NOAA',22.6393, 113.8107, 'C', 'highest-temp-shenzhen'),
-- Note: CHM00056294 (Shuangliu) has TAVG only; Wenjiang (56187) is 17km proxy with TMAX.
('Chengdu',       'CHM00056187', 'Wenjiang (nr Chengdu Shuangliu)',     'NOAA', 30.7500, 103.8670, 'C', 'highest-temp-chengdu'),
('Chongqing',     'CHM00057516', 'Chongqing Jiangbei International Airport','NOAA',29.7192,106.6417,'C', 'highest-temp-chongqing'),
('Wuhan',         'CHM00057494', 'Wuhan Tianhe International Airport',   'NOAA', 30.7838, 114.2080, 'C', 'highest-temp-wuhan'),

-- ── SOUTHEAST ASIA (°C) ─────────────────────────────────────
-- Note: Changi (SNM00048698) has no TMAX; Batam/Hang Nadim is 30km proxy.
('Singapore',     'IDM00096087', 'Batam/Hang Nadim (proxy for Changi)', 'NOAA',  1.1170, 104.1170, 'C', 'highest-temp-singapore'),
-- Note: MAM prefix wrong (Morocco); correct country is MY (Malaysia).
('Kuala Lumpur',  'MYM00048650', 'Kuala Lumpur International Airport',  'NOAA',  2.7460, 101.7100, 'C', 'highest-temp-kuala-lumpur'),
-- Note: RPM prefix wrong; correct is RP0.
('Manila',        'RP000098429', 'Ninoy Aquino International Airport',  'NOAA', 14.5170, 121.0000, 'C', 'highest-temp-manila'),
('Jakarta',       'IDM00096749', 'Halim Perdanakusuma International Airport','NOAA',-6.2664,106.8906,'C','highest-temp-jakarta'),

-- ── SOUTH ASIA (°C) ─────────────────────────────────────────
-- Note: INM00042275 not in GHCN; correct is IN023351400 (Lucknow/Amausi, WMO 42369).
('Lucknow',       'IN023351400', 'Lucknow/Amausi Airport',              'NOAA', 26.7500,  80.8830, 'C', 'highest-temp-lucknow'),
-- Note: Masroor Airbase (PAF) has no GHCN station. Using Jinnah Intl as proxy.
('Karachi',       'PKM00041780', 'Karachi Jinnah Intl (proxy)',         'NOAA', 24.8960,  66.9380, 'C', 'highest-temp-karachi'),

-- ── OCEANIA (°C) ────────────────────────────────────────────
-- Confidence: HIGH
('Wellington',    'NZM00093439', 'Wellington International Airport',     'NOAA',-41.3272, 174.8047, 'C', 'highest-temp-wellington'),

-- ── AMERICAS — NON-US (°C) ──────────────────────────────────
-- Note: CAM prefix wrong; correct is CA006158355 (Toronto City; Pearson has no TMAX).
('Toronto',       'CA006158355', 'Toronto City',                         'NOAA', 43.6667,  -79.4000,'C', 'highest-temp-toronto'),
-- Confidence: HIGH (GSN station)
('Mexico City',   'MXM00076680', 'Benito Juárez International Airport',  'NOAA', 19.4000,  -99.1830,'C', 'highest-temp-mexico-city'),
-- Note: No GHCN TMAX near Guarulhos; delta=0.
('São Paulo',     'BRM00083004', 'São Paulo (no GHCN TMAX; delta=0)',   'NOAA',-23.4356,  -46.4731,'C', 'highest-temp-sao-paulo'),
-- Confidence: HIGH
('Buenos Aires',  'ARM00087576', 'Ministro Pistarini International',     'NOAA',-34.8220,  -58.5360,'C', 'highest-temp-buenos-aires'),
-- Note: PMM00078762 not in GHCN; no TMAX at any Panama station; delta=0.
('Panama City',   'PMM00078762', 'Tocumen Intl (no GHCN TMAX; delta=0)','NOAA', 9.0700,  -79.3800,'C', 'highest-temp-panama-city'),

-- ── AFRICA (°C) ─────────────────────────────────────────────
-- Confidence: HIGH (GSN station)
('Cape Town',     'SFM00068816', 'Cape Town International Airport',      'NOAA',-33.9650,  18.6020, 'C', 'highest-temp-cape-town'),
-- Note: NGM prefix wrong; correct is NIM (Nigeria).
('Lagos',         'NIM00065201', 'Murtala Muhammad International Airport','NOAA', 6.5770,   3.3210, 'C', 'highest-temp-lagos');
