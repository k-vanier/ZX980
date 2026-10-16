package fr.zx980.tracker

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class SettingsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_settings)
        applyInsets(findViewById(R.id.root))

        val url = findViewById<EditText>(R.id.url)
        val user = findViewById<EditText>(R.id.user)
        val password = findViewById<EditText>(R.id.password)
        val result = findViewById<TextView>(R.id.result)
        val save = findViewById<Button>(R.id.save)
        Prefs.load(this).let {
            url.setText(it.url)
            user.setText(it.user)
            password.setText(it.password)
        }

        save.setOnClickListener {
            var u = url.text.toString().trim()
            if (u.isNotEmpty() && !u.contains("://")) u = "http://$u"
            url.setText(u)
            val cfg = Config(u, user.text.toString().trim(), password.text.toString())
            Prefs.save(this, cfg)
            if (u.isEmpty()) {
                result.text = "Indiquez l'adresse du serveur."
                return@setOnClickListener
            }
            result.text = "Test de la connexion…"
            save.isEnabled = false
            lifecycleScope.launch {
                val error = try {
                    withContext(Dispatchers.IO) { Api.state(cfg, "hours=1") }
                    null
                } catch (e: Exception) {
                    e.message ?: e.javaClass.simpleName
                }
                save.isEnabled = true
                if (error == null) {
                    Toast.makeText(this@SettingsActivity, "Connexion réussie", Toast.LENGTH_SHORT).show()
                    finish()
                } else {
                    result.text = "Enregistré, mais la connexion a échoué : $error"
                }
            }
        }
        findViewById<Button>(R.id.close).setOnClickListener { finish() }
    }
}
