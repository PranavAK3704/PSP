package com.meesho.valmo.prism;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;

import com.meesho.prism.PrismDW;
import com.meesho.prism.constants.FetchType;
import com.meesho.prism.constants.PrismSortOrder;
import com.meesho.prism.exception.EngineException;
import com.meesho.prism.response.EngineResponse;

/**
 * prism-sidecar — exposes Meesho PrismSDK to the Python backend over loopback HTTP.
 *
 * PrismSDK is a Java client, so a Python service cannot call it in-process. This shim is the
 * bridge. It is deliberately DUMB: it does not decide what to query. The Python side owns the
 * named-query whitelist and sends already-validated structured fetch parameters
 * (table / columns / filter / date window / sort / limit). The sidecar never accepts SQL and never
 * builds a filter itself.
 *
 * Endpoints
 *   GET  /health  -> {"ok":true,"token_present":bool,"environment":"PRODUCTION|SANDBOX"}
 *   POST /fetch   -> {"rows":[{...}], "rowCount":n, "queryName":"..."}
 *                    body: {table, columns[], filter, startDate, endDate, sortOrder{}, limit, fetchType}
 *
 * Security posture
 *   • Binds to 127.0.0.1 by default (set PRISM_SIDECAR_BIND=0.0.0.0 only for a k8s sidecar where
 *     the pod network is the boundary). Nothing here is authenticated — it must never be exposed.
 *   • The client_token is read from the environment by PrismSDK itself. This process never logs it,
 *     never echoes it, and /health reports only whether it is PRESENT.
 *   • Read-only: only fetch* APIs are used. There is no write path.
 *
 * Env: client_token (PrismSDK auth), PRISMSDK_ENVIRONMENT=PRODUCTION|SANDBOX,
 *      PRISM_SIDECAR_PORT (default 8099), PRISM_SIDECAR_BIND (default 127.0.0.1),
 *      PRISM_MAX_ROWS (hard row cap, default 5000).
 */
public final class SidecarMain {

    private static final ObjectMapper M = new ObjectMapper();
    private static final int DEFAULT_PORT = 8099;
    private static final int DEFAULT_MAX_ROWS = 5000;

    public static void main(String[] args) throws IOException {
        int port = envInt("PRISM_SIDECAR_PORT", DEFAULT_PORT);
        String bind = env("PRISM_SIDECAR_BIND", "127.0.0.1");

        HttpServer server = HttpServer.create(new InetSocketAddress(InetAddress.getByName(bind), port), 0);
        server.createContext("/health", SidecarMain::health);
        server.createContext("/fetch", SidecarMain::fetch);
        server.setExecutor(Executors.newFixedThreadPool(4));
        server.start();
        System.out.println("prism-sidecar listening on " + bind + ":" + port
                + " environment=" + env("PRISMSDK_ENVIRONMENT", "SANDBOX")
                + " token_present=" + tokenPresent());
    }

    // ── GET /health ──────────────────────────────────────────────────────────
    private static void health(HttpExchange ex) throws IOException {
        ObjectNode out = M.createObjectNode();
        out.put("ok", true);
        out.put("token_present", tokenPresent());          // never the value
        out.put("environment", env("PRISMSDK_ENVIRONMENT", "SANDBOX"));
        out.put("detail", tokenPresent() ? "sidecar up" : "sidecar up, client_token NOT set");
        respond(ex, 200, out);
    }

    // ── POST /fetch ──────────────────────────────────────────────────────────
    private static void fetch(HttpExchange ex) throws IOException {
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            respond(ex, 405, error("method not allowed", "405"));
            return;
        }
        JsonNode req;
        try (InputStream in = ex.getRequestBody()) {
            req = M.readTree(in.readAllBytes());
        } catch (Exception e) {
            respond(ex, 400, error("invalid JSON body", "400"));
            return;
        }

        String table = text(req, "table");
        String queryName = text(req, "queryName");
        if (table.isEmpty()) {
            respond(ex, 400, error("'table' is required", "400"));
            return;
        }
        // Defence in depth: the Python whitelist should never send SQL, so reject anything that
        // looks like a statement rather than a table identifier.
        String lower = table.toLowerCase();
        if (lower.contains(" ") || lower.contains(";") || lower.contains("select")) {
            respond(ex, 400, error("'table' must be a plain schema.table identifier", "400"));
            return;
        }

        List<String> columns = new ArrayList<>();
        if (req.has("columns") && req.get("columns").isArray()) {
            req.get("columns").forEach(c -> columns.add(c.asText()));
        }
        if (columns.isEmpty()) {
            respond(ex, 400, error("'columns' must be a non-empty array", "400"));
            return;
        }

        String filter = req.hasNonNull("filter") ? req.get("filter").asText() : null;
        String startDate = req.hasNonNull("startDate") ? req.get("startDate").asText() : null;
        String endDate = req.hasNonNull("endDate") ? req.get("endDate").asText() : null;
        int maxRows = envInt("PRISM_MAX_ROWS", DEFAULT_MAX_ROWS);
        int limit = req.hasNonNull("limit") ? Math.min(req.get("limit").asInt(), maxRows) : maxRows;

        Map<String, PrismSortOrder> sortOrder = new LinkedHashMap<>();
        if (req.has("sortOrder") && req.get("sortOrder").isObject()) {
            req.get("sortOrder").fields().forEachRemaining(e -> {
                String dir = e.getValue().asText("ASCENDING").toUpperCase();
                sortOrder.put(e.getKey(),
                        dir.startsWith("DESC") ? PrismSortOrder.DESCENDING : PrismSortOrder.ASCENDING);
            });
        }
        String fetchType = req.hasNonNull("fetchType") ? req.get("fetchType").asText() : FetchType.REST;

        // Partitioned (silver.*) tables REQUIRE a date window — fail clearly rather than letting
        // PrismSDK reject it with a less obvious message.
        if (table.startsWith("silver.") && (startDate == null || endDate == null)) {
            respond(ex, 400, error("partitioned table '" + table + "' requires startDate and endDate", "400"));
            return;
        }

        long t0 = System.currentTimeMillis();
        try {
            PrismDW prismDW = PrismDW.getInstance();
            EngineResponse resp = prismDW.fetchData(table, columns, filter, startDate, endDate,
                    sortOrder.isEmpty() ? null : sortOrder, limit, LinkedHashMap.class, fetchType);

            // Drain the iterator (PrismSDK batches at ~45–50k) up to our cap.
            List<Object> rows = new ArrayList<>();
            while (resp != null && resp.hasNext() && rows.size() < limit) {
                List<?> batch = resp.extractData(LinkedHashMap.class);
                if (batch == null || batch.isEmpty()) {
                    break;
                }
                for (Object row : batch) {
                    if (rows.size() >= limit) {
                        break;
                    }
                    rows.add(row);
                }
                resp = resp.next();
            }

            ObjectNode out = M.createObjectNode();
            out.set("rows", M.valueToTree(rows));
            out.put("rowCount", rows.size());
            out.put("queryName", queryName);
            out.put("tookMs", System.currentTimeMillis() - t0);
            respond(ex, 200, out);
            System.out.println("fetch ok query=" + queryName + " table=" + table
                    + " rows=" + rows.size() + " tookMs=" + (System.currentTimeMillis() - t0));

        } catch (EngineException e) {
            // Surface PrismSDK's own code so the Python side can distinguish
            // 401 unauthorised / 409 unknown table-or-column / 500 escalate-to-data-team.
            String code = String.valueOf(e.getErrorCode());
            int http = "401".equals(code) ? 401 : "409".equals(code) ? 409 : 502;
            System.out.println("fetch FAILED query=" + queryName + " table=" + table + " code=" + code);
            respond(ex, http, error(safe(e.getMessage()), code));
        } catch (Exception e) {
            System.out.println("fetch ERROR query=" + queryName + " " + e.getClass().getSimpleName());
            respond(ex, 502, error(e.getClass().getSimpleName() + ": " + safe(e.getMessage()), "500"));
        }
    }

    // ── helpers ──────────────────────────────────────────────────────────────
    private static boolean tokenPresent() {
        // PrismSDK reads client_token from the environment; report presence only.
        String t = System.getenv("client_token");
        if (t == null || t.isBlank()) {
            t = System.getProperty("client.token");
        }
        return t != null && !t.isBlank();
    }

    /** Never let a credential reach a response/log through an exception message. */
    private static String safe(String msg) {
        if (msg == null) {
            return "";
        }
        String token = System.getenv("client_token");
        if (token != null && !token.isBlank()) {
            msg = msg.replace(token, "<redacted>");
        }
        return msg.length() > 500 ? msg.substring(0, 500) : msg;
    }

    private static ObjectNode error(String message, String code) {
        ObjectNode out = M.createObjectNode();
        out.put("ok", false);
        out.put("code", code);
        out.put("message", message);
        return out;
    }

    private static void respond(HttpExchange ex, int status, ObjectNode body) throws IOException {
        byte[] payload = M.writeValueAsBytes(body);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, payload.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(payload);
        }
    }

    private static String text(JsonNode n, String field) {
        return n.hasNonNull(field) ? n.get(field).asText().trim() : "";
    }

    private static String env(String key, String dflt) {
        String v = System.getenv(key);
        return (v == null || v.isBlank()) ? dflt : v;
    }

    private static int envInt(String key, int dflt) {
        try {
            return Integer.parseInt(env(key, String.valueOf(dflt)));
        } catch (NumberFormatException e) {
            return dflt;
        }
    }

    private SidecarMain() {
    }
}
