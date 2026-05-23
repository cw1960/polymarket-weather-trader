/**
 * Polymarket CLOB v2 execution service.
 *
 * Local HTTP shim that wraps the @polymarket/clob-client-v2 TS client
 * because the Python equivalent (py_clob_client_v2) has a bug in its L1
 * auth handshake. Python executor talks to this server over loopback.
 *
 * Routes:
 *   GET  /health                       — liveness check
 *   POST /place-order                  — create + post a GTC/FOK/FAK limit order
 *   POST /cancel-order                 — cancel an open order by orderID
 *   GET  /order?orderID=…              — fetch a single order's state
 *   GET  /book?tokenID=…               — public order book snapshot
 *   GET  /market?conditionID=…         — market metadata (tick_size, neg_risk)
 *   GET  /balance-allowance            — USDC balance + allowance on the funder
 *
 * Listens only on 127.0.0.1 (loopback). Never expose externally.
 */
import http from "node:http";
import { createWalletClient, http as viemHttp } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { polygon } from "viem/chains";
import {
  ClobClient,
  OrderType,
  Side,
  SignatureTypeV2,
} from "@polymarket/clob-client-v2";
import { config as dotenvConfig } from "dotenv";

dotenvConfig({ path: "/root/polymarket/.env" });

const PORT = parseInt(process.env.TS_EXECUTOR_PORT || "8787", 10);
const HOST = "https://clob.polymarket.com";

const PK_RAW = process.env.POLY_PRIVATE_KEY || "";
if (!PK_RAW) {
  console.error("FATAL: POLY_PRIVATE_KEY missing in /root/polymarket/.env");
  process.exit(1);
}
const PK = PK_RAW.startsWith("0x") ? PK_RAW : "0x" + PK_RAW;

const FUNDER = process.env.POLY_FUNDER_ADDRESS || "";
if (!FUNDER) {
  console.error("FATAL: POLY_FUNDER_ADDRESS missing in /root/polymarket/.env");
  process.exit(1);
}

const account = privateKeyToAccount(PK);
const walletClient = createWalletClient({
  account,
  chain: polygon,
  transport: viemHttp(),
});

console.log(`[ts-exec] EOA:    ${account.address}`);
console.log(`[ts-exec] Funder: ${FUNDER}`);

// ── Client bootstrap ────────────────────────────────────────────────────────
// First derive API creds via an EOA-only client (no funder/sig_type),
// then build the real trading client with funder + POLY_1271.

let client = null;

async function initClient() {
  const tempClient = new ClobClient({
    host: HOST,
    chain: 137,
    signer: walletClient,
  });
  const creds = await tempClient.createOrDeriveApiKey();
  console.log(`[ts-exec] API key derived: ${creds.key}`);

  client = new ClobClient({
    host: HOST,
    chain: 137,
    signer: walletClient,
    creds,
    signatureType: SignatureTypeV2.POLY_1271,
    funderAddress: FUNDER,
  });
  console.log(`[ts-exec] Trading client ready (POLY_1271, funder ${FUNDER})`);
}

// ── Request timeout wrapper ────────────────────────────────────────────────
// Polymarket occasionally takes minutes to respond.  Without a hard cap,
// our server blocks every other caller until the slow request finishes.
// Wrap each upstream call so it returns 504 if it doesn't complete in time.
const UPSTREAM_TIMEOUT_MS = parseInt(
  process.env.TS_UPSTREAM_TIMEOUT_MS || "20000",
  10,
);

function withTimeout(promise, ms, label) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => {
      const e = new Error(`upstream timeout after ${ms}ms (${label})`);
      e.code = "ETIMEOUT_UPSTREAM";
      reject(e);
    }, ms);
    promise.then(
      (v) => { clearTimeout(t); resolve(v); },
      (e) => { clearTimeout(t); reject(e); },
    );
  });
}

// ── HTTP helpers ────────────────────────────────────────────────────────────
function send(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf8");
        resolve(raw ? JSON.parse(raw) : {});
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

// Extract a usable error message from any thrown thing
function errMsg(e) {
  if (!e) return "unknown error";
  if (typeof e === "string") return e;
  // Polymarket SDK wraps Axios errors; the useful bits are in response.data
  const data = e?.response?.data;
  if (data) return typeof data === "string" ? data : JSON.stringify(data);
  return e.message || String(e);
}

// Resolve OrderType from string (defaults to GTC)
function resolveOrderType(s) {
  switch ((s || "GTC").toUpperCase()) {
    case "GTC": return OrderType.GTC;
    case "FOK": return OrderType.FOK;
    case "FAK": return OrderType.FAK;
    case "GTD": return OrderType.GTD;
    default:    return OrderType.GTC;
  }
}

// ── Route handlers ──────────────────────────────────────────────────────────
async function handlePlaceOrder(body) {
  const { tokenID, price, size, side, tickSize, negRisk, orderType } = body;
  if (!tokenID || price == null || size == null || !side) {
    throw new Error("missing required fields: tokenID, price, size, side");
  }
  const orderSide = side.toUpperCase() === "SELL" ? Side.SELL : Side.BUY;
  const opts = {};
  if (tickSize != null) opts.tickSize = String(tickSize);
  if (negRisk != null)  opts.negRisk  = Boolean(negRisk);

  const resp = await client.createAndPostOrder(
    {
      tokenID: String(tokenID),
      price: Number(price),
      side: orderSide,
      size: Number(size),
    },
    opts,
    resolveOrderType(orderType),
  );

  // Polymarket sometimes returns HTTP 200 with an error body
  // (e.g. {"error":"invalid signature","status":400}). The SDK passes
  // these through without throwing, so we have to surface them ourselves.
  // Without this, callers see a 200 and treat a failed order as success.
  const errField = resp?.error || resp?.errorMsg;
  const orderID  = resp?.orderID || resp?.order_id;
  const succeed  = resp?.success !== false;       // undefined → assume ok
  if (errField || (!orderID && !succeed)) {
    const err = new Error(errField || "order rejected without orderID");
    err.polymarketResponse = resp;
    throw err;
  }
  return resp;
}

async function handleCancelOrder(body) {
  const { orderID } = body;
  if (!orderID) throw new Error("missing orderID");
  return await client.cancelOrder({ orderID: String(orderID) });
}

async function handleGetOrder(url) {
  const orderID = url.searchParams.get("orderID");
  if (!orderID) throw new Error("missing orderID query param");
  return await client.getOrder(orderID);
}

async function handleGetBook(url) {
  const tokenID = url.searchParams.get("tokenID");
  if (!tokenID) throw new Error("missing tokenID query param");
  return await client.getOrderBook(tokenID);
}

async function handleGetMarket(url) {
  const conditionID = url.searchParams.get("conditionID");
  if (!conditionID) throw new Error("missing conditionID query param");
  return await client.getMarket(conditionID);
}

async function handleGetBalanceAllowance(url) {
  // Default: USDC collateral on the funder
  return await client.getBalanceAllowance({
    asset_type: "COLLATERAL",
    signature_type: SignatureTypeV2.POLY_1271,
  });
}

// ── Main request handler ────────────────────────────────────────────────────
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const route = `${req.method} ${url.pathname}`;
  const t0 = Date.now();

  try {
    if (!client && url.pathname !== "/health") {
      return send(res, 503, { error: "client not yet initialised" });
    }

    let payload;
    switch (route) {
      case "GET /health":
        payload = {
          ok: true,
          eoa: account.address,
          funder: FUNDER,
          client_ready: client !== null,
        };
        break;
      case "POST /place-order":
        payload = await withTimeout(handlePlaceOrder(await readBody(req)),
                                    UPSTREAM_TIMEOUT_MS, "place-order");
        break;
      case "POST /cancel-order":
        payload = await withTimeout(handleCancelOrder(await readBody(req)),
                                    UPSTREAM_TIMEOUT_MS, "cancel-order");
        break;
      case "GET /order":
        payload = await withTimeout(handleGetOrder(url),
                                    UPSTREAM_TIMEOUT_MS, "order");
        break;
      case "GET /book":
        payload = await withTimeout(handleGetBook(url),
                                    UPSTREAM_TIMEOUT_MS, "book");
        break;
      case "GET /market":
        payload = await withTimeout(handleGetMarket(url),
                                    UPSTREAM_TIMEOUT_MS, "market");
        break;
      case "GET /balance-allowance":
        payload = await withTimeout(handleGetBalanceAllowance(url),
                                    UPSTREAM_TIMEOUT_MS, "balance-allowance");
        break;
      default:
        return send(res, 404, { error: `no route for ${route}` });
    }

    const ms = Date.now() - t0;
    console.log(`[ts-exec] ${route} → 200 (${ms}ms)`);
    send(res, 200, payload);
  } catch (e) {
    const ms = Date.now() - t0;
    const msg = errMsg(e);
    const isTimeout = e && e.code === "ETIMEOUT_UPSTREAM";
    const status   = isTimeout ? 504 : 500;
    console.error(`[ts-exec] ${route} → ${status} (${ms}ms): ${msg}`);
    send(res, status, { error: msg });
  }
});

// ── Boot ────────────────────────────────────────────────────────────────────
initClient()
  .then(() => {
    server.listen(PORT, "127.0.0.1", () => {
      console.log(`[ts-exec] listening on 127.0.0.1:${PORT}`);
    });
  })
  .catch((e) => {
    console.error(`[ts-exec] FATAL: client init failed — ${errMsg(e)}`);
    process.exit(1);
  });

// Graceful shutdown
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    console.log(`[ts-exec] received ${sig}, shutting down`);
    server.close(() => process.exit(0));
  });
}
