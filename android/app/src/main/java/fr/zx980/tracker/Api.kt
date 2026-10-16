package fr.zx980.tracker

import android.content.Context
import android.util.Base64
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** Adresse du tableau de bord (port 8090) et accès HTTP Basic */
class Config(val url: String, val user: String, val password: String)

object Prefs {
    private fun sp(ctx: Context) = ctx.getSharedPreferences("zx980", Context.MODE_PRIVATE)

    fun load(ctx: Context) = sp(ctx).run {
        Config(getString("url", "")!!, getString("user", "")!!, getString("password", "")!!)
    }

    fun save(ctx: Context, cfg: Config) = sp(ctx).edit()
        .putString("url", cfg.url)
        .putString("user", cfg.user)
        .putString("password", cfg.password)
        .apply()
}

object Api {
    /** GET /api/state?<query> : même JSON que celui lu par la page web */
    fun state(cfg: Config, query: String): JSONObject {
        val c = URL(cfg.url.trimEnd('/') + "/api/state?" + query).openConnection() as HttpURLConnection
        c.connectTimeout = 8000
        c.readTimeout = 15000
        if (cfg.user.isNotEmpty()) {
            val token = Base64.encodeToString("${cfg.user}:${cfg.password}".toByteArray(), Base64.NO_WRAP)
            c.setRequestProperty("Authorization", "Basic $token")
        }
        try {
            val code = c.responseCode
            if (code == 401) throw IOException("identifiant ou mot de passe refusé")
            if (code !in 200..299) throw IOException("HTTP $code")
            return JSONObject(c.inputStream.bufferedReader().use { it.readText() })
        } finally {
            c.disconnect()
        }
    }
}

// Les champs absents ou à null dans le JSON du serveur
fun JSONObject.str(k: String): String? = if (isNull(k)) null else optString(k)
fun JSONObject.num(k: String): Double? = if (isNull(k)) null else optDouble(k).takeUnless { it.isNaN() }
fun JSONObject.obj(k: String): JSONObject? = if (isNull(k)) null else optJSONObject(k)
