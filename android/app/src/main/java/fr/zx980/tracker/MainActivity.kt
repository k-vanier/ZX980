package fr.zx980.tracker

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.osmdroid.config.Configuration
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.BoundingBox
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.MapView
import org.osmdroid.views.overlay.Marker
import org.osmdroid.views.overlay.Polyline
import java.io.File
import java.net.URLEncoder
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.util.Locale

private const val OK = 0xFF16A34A.toInt()
private const val WARN = 0xFFD97706.toInt()
private const val BAD = 0xFFDC2626.toInt()
private const val REFRESH_MS = 5000L
private const val MAX_ROWS = 100

/** Padding des barres système (Android 15 dessine l'appli sous la barre d'état) */
fun applyInsets(v: View) = ViewCompat.setOnApplyWindowInsetsListener(v) { view, ins ->
    val b = ins.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
    view.setPadding(b.left, b.top, b.right, b.bottom)
    WindowInsetsCompat.CONSUMED
}

class MainActivity : ComponentActivity() {
    private val dp by lazy { resources.displayMetrics.density }
    private lateinit var map: MapView
    private lateinit var track: Polyline
    private lateinit var posMarker: Marker
    private lateinit var startMarker: Marker
    private lateinit var status: TextView
    private lateinit var rangeBar: View
    private lateinit var fromBtn: Button
    private lateinit var toBtn: Button

    private val periods = arrayOf("1 h", "6 h", "24 h", "7 j", "Période choisie")
    private val periodHours = arrayOf(1, 6, 24, 168, null)
    private var hours: Int? = 24  // null = période choisie (du / au)
    private var from: LocalDateTime = LocalDate.now().atStartOfDay()
    private var to: LocalDateTime = LocalDateTime.now()

    private var srvTz = "+00:00"
    private var centered = false
    private var fitPending = false
    private var lastPos: GeoPoint? = null
    private val lock = Mutex()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // osmdroid : user agent obligatoire pour les tuiles OSM, cache dans le dossier de l'appli
        Configuration.getInstance().apply {
            load(applicationContext, getSharedPreferences("osmdroid", MODE_PRIVATE))
            userAgentValue = packageName
            osmdroidBasePath = File(cacheDir, "osmdroid")
            osmdroidTileCache = File(cacheDir, "osmdroid/tiles")
        }
        setContentView(R.layout.activity_main)
        applyInsets(findViewById(R.id.root))

        map = findViewById(R.id.map)
        map.setTileSource(TileSourceFactory.MAPNIK)
        map.setMultiTouchControls(true)
        map.controller.setZoom(6.0)
        map.controller.setCenter(GeoPoint(46.6, 2.4))
        track = Polyline(map).apply {
            outlinePaint.color = 0xFF2563EB.toInt()
            outlinePaint.strokeWidth = 4 * dp
        }
        map.overlays.add(track)
        startMarker = dot(OK, 14)
        posMarker = dot(BAD, 16)

        status = findViewById(R.id.status)
        rangeBar = findViewById(R.id.rangeBar)
        fromBtn = findViewById(R.id.from)
        toBtn = findViewById(R.id.to)
        updateRange()
        fromBtn.setOnClickListener { pick(from) { from = it; changed() } }
        toBtn.setOnClickListener { pick(to) { to = it; changed() } }

        val spinner = findViewById<Spinner>(R.id.period)
        spinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, periods)
        spinner.setSelection(periodHours.indexOf(hours))
        spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                if (periodHours[pos] == hours) return
                hours = periodHours[pos]
                rangeBar.isVisible = hours == null
                changed()
            }

            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        findViewById<View>(R.id.settings).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        findViewById<View>(R.id.recenter).setOnClickListener {
            lastPos?.let { map.controller.animateTo(it, 17.0, 600L) }
        }
        findViewById<View>(R.id.openMaps).setOnClickListener {
            lastPos?.let {
                val q = "${it.latitude},${it.longitude}"
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("geo:$q?q=$q")))
            }
        }

        if (savedInstanceState == null && Prefs.load(this).url.isEmpty()) {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        // rafraîchi toutes les 5 s tant que l'appli est visible, comme la page web
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    refresh()
                    delay(REFRESH_MS)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        map.onResume()
    }

    override fun onPause() {
        super.onPause()
        map.onPause()
    }

    private fun dot(color: Int, sizeDp: Int) = Marker(map).apply {
        val px = (sizeDp * dp).toInt()
        icon = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(color)
            setStroke((2 * dp).toInt(), Color.WHITE)
            setSize(px, px)
        }
        setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
        setInfoWindow(null)
        setOnMarkerClickListener { _, _ -> true }
    }

    private fun pick(init: LocalDateTime, done: (LocalDateTime) -> Unit) {
        DatePickerDialog(this, { _, y, m, d ->
            TimePickerDialog(this, { _, h, min ->
                done(LocalDateTime.of(y, m + 1, d, h, min))
            }, init.hour, init.minute, true).show()
        }, init.year, init.monthValue - 1, init.dayOfMonth).show()
    }

    private fun updateRange() {
        fromBtn.text = "du " + Times.pick(from)
        toBtn.text = "au " + Times.pick(to)
    }

    /** période changée : recadrer la carte sur la trace au prochain résultat */
    private fun changed() {
        updateRange()
        fitPending = true
        lifecycleScope.launch { refresh() }
    }

    private fun query(): String {
        val h = hours ?: return "from=" + enc(Times.toServer(from, srvTz)) +
            "&to=" + enc(Times.toServer(to.withSecond(59), srvTz))
        return "hours=$h"
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

    private suspend fun refresh() = lock.withLock {
        val cfg = Prefs.load(this)
        if (cfg.url.isEmpty()) {
            setStatus("serveur non réglé", BAD)
            return@withLock
        }
        val s = try {
            withContext(Dispatchers.IO) { Api.state(cfg, query()) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            setStatus("erreur : " + (e.message ?: e.javaClass.simpleName), BAD)
            return@withLock
        }
        srvTz = s.optString("tz", srvTz)
        render(s)
    }

    private fun setStatus(text: String, color: Int) {
        status.text = text
        status.background = GradientDrawable().apply {
            cornerRadius = 10 * dp
            setColor(color)
        }
    }

    private fun render(s: JSONObject) {
        val seen = Times.parse(s.str("last_seen"))
        val now = Times.parse(s.str("now"))
        if (seen != null && now != null) {
            val age = Duration.between(seen, now).seconds
            val text = when {
                age < 60 -> "en ligne · $age s"
                age < 3600 -> "vu il y a ${(age + 30) / 60} min"
                else -> "hors ligne · " + Times.local(s.str("last_seen"), srvTz)
            }
            setStatus(text, if (age < 30) OK else if (age < 300) WARN else BAD)
        } else {
            setStatus("aucun message", BAD)
        }

        val none = listOf("Aucune donnée sur la période" to "")
        val p = s.obj("last_pos")
        val pos = p?.let { GeoPoint(it.getDouble("lat"), it.getDouble("lon")) }
        lastPos = pos
        fill(R.id.pos, if (p == null) none else listOf(
            "Heure GPS" to Times.device(p.str("device_time")),
            "Latitude" to fmt(p.num("lat"), 6),
            "Longitude" to fmt(p.num("lon"), 6),
            "Altitude" to fmt(p.num("alt"), 1, " m"),
            "Vitesse" to fmt(p.num("speed"), 1, " km/h")))
        findViewById<View>(R.id.openMaps).isVisible = pos != null
        if (pos != null && !centered && hours != null) {
            map.controller.setZoom(17.0)
            map.controller.setCenter(pos)
            centered = true
        }

        val d = s.obj("last_full")
        fill(R.id.dev, if (d == null) none else listOf(
            "Batterie" to battery(d.num("battery")),
            "Fix GPS" to (d.str("fix") ?: "—"),
            "Réseau" to (d.str("network") ?: "—"),
            "Opérateur" to (d.str("operator") ?: "—"),
            "IP" to (d.str("ip") ?: "—"),
            "Téléphone" to (d.str("phone") ?: "—"),
            "Dernier état" to Times.local(d.str("received"), srvTz)))

        val arr = s.getJSONArray("track")
        val pts = (0 until arr.length()).map { arr.getJSONArray(it).let { a -> GeoPoint(a.getDouble(0), a.getDouble(1)) } }
        track.setPoints(pts)
        // ordre d'affichage : trace, départ (vert), dernière position (rouge)
        map.overlays.remove(startMarker)
        map.overlays.remove(posMarker)
        pts.firstOrNull()?.let { startMarker.position = it; map.overlays.add(startMarker) }
        pos?.let { posMarker.position = it; map.overlays.add(posMarker) }
        if (fitPending) {
            fitPending = false
            fit(pts)
        }
        map.invalidate()

        val st = s.getJSONObject("stats")
        val start = s.optString("start")
        val end = s.optString("end")
        fill(R.id.trip, if (start > end) listOf("La date de début est après la date de fin" to "") else listOf(
            "Du" to Times.local(start, srvTz),
            "Au" to Times.local(end, srvTz),
            "Points" to st.optInt("points").toString(),
            "Distance" to fmt(st.num("km"), 2, " km"),
            "Vitesse max" to fmt(st.num("max_speed"), 1, " km/h")))

        val recent = s.getJSONArray("recent")
        findViewById<TextView>(R.id.count).text = "Messages de la période (${s.optInt("count")})"
        val sb = StringBuilder()
        for (i in 0 until minOf(recent.length(), MAX_ROWS)) {
            val r = recent.getJSONObject(i)
            sb.append(Times.local(r.str("received"), srvTz)).append("  ")
            if (r.str("kind") == "other") {
                sb.append("brut  ").append(r.str("raw") ?: "")
            } else {
                sb.append(String.format(Locale.ROOT, "%-4s  ", r.str("kind")))
                    .append(fmt(r.num("lat"), 5)).append(", ").append(fmt(r.num("lon"), 5))
                    .append("  ").append(fmt(r.num("speed"), 1, " km/h"))
                r.num("battery")?.let { sb.append("  ").append(it.toInt()).append(" %") }
            }
            sb.append('\n')
        }
        if (recent.length() > MAX_ROWS) sb.append("… (les $MAX_ROWS plus récents)")
        findViewById<TextView>(R.id.rows).text = sb
    }

    private fun fit(pts: List<GeoPoint>) {
        if (pts.isEmpty()) return
        val box = BoundingBox.fromGeoPointsSafe(pts)
        map.post {
            if (box.latitudeSpan < 1e-5 && box.longitudeSpanWithDateLine < 1e-5) {
                map.controller.setZoom(17.0)
                map.controller.setCenter(pts.last())
            } else {
                map.zoomToBoundingBox(box, false, (30 * dp).toInt(), 17.0, null)
            }
        }
    }

    private fun battery(v: Double?): CharSequence {
        val b = v?.toInt() ?: return "—"
        val text = SpannableString("$b %")
        val color = if (b > 50) OK else if (b > 20) WARN else BAD
        text.setSpan(ForegroundColorSpan(color), 0, text.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        return text
    }

    private fun fmt(v: Double?, digits: Int, unit: String = "") =
        v?.let { String.format(Locale.ROOT, "%.${digits}f", it) + unit } ?: "—"

    /** une carte d'infos : une ligne « libellé  valeur » par élément */
    private fun fill(id: Int, rows: List<Pair<String, CharSequence>>) {
        val box = findViewById<LinearLayout>(id)
        box.removeAllViews()
        for ((k, v) in rows) {
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            row.addView(TextView(this).apply {
                text = k
                setTextColor(0xFF6B7280.toInt())
                if (v.isNotEmpty()) width = (110 * dp).toInt()
            })
            row.addView(TextView(this).apply {
                text = v
                setTextColor(0xFF111827.toInt())
                setTextIsSelectable(true)
            })
            box.addView(row)
        }
    }
}
