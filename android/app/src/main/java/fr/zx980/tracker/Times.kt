package fr.zx980.tracker

import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/**
 * Les heures du serveur sont sans fuseau, dans le fuseau du serveur (souvent UTC sur FreeBSD).
 * /api/state donne ce fuseau dans "tz" (ex. +00:00) : on convertit vers et depuis celui du téléphone.
 * device_time est déjà l'heure de Paris calculée par le traceur : elle est affichée telle quelle.
 */
object Times {
    private val ISO = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss")
    private val SHORT = DateTimeFormatter.ofPattern("dd/MM HH:mm:ss")
    private val FULL = DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm:ss")
    private val PICK = DateTimeFormatter.ofPattern("dd/MM HH:mm")

    fun parse(s: String?): LocalDateTime? = s?.let { runCatching { LocalDateTime.parse(it) }.getOrNull() }

    private fun zone(tz: String) = runCatching { ZoneOffset.of(tz) }.getOrDefault(ZoneOffset.UTC)

    /** heure du serveur -> heure du téléphone */
    fun local(s: String?, tz: String): String =
        parse(s)?.atOffset(zone(tz))?.atZoneSameInstant(ZoneId.systemDefault())?.format(SHORT) ?: "—"

    /** heure du téléphone -> heure du serveur, pour ?from= et ?to= */
    fun toServer(t: LocalDateTime, tz: String): String =
        t.atZone(ZoneId.systemDefault()).withZoneSameInstant(zone(tz)).toLocalDateTime().format(ISO)

    fun device(s: String?): String = parse(s)?.format(FULL) ?: "—"

    fun pick(t: LocalDateTime): String = t.format(PICK)
}
