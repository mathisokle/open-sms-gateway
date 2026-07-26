# Message format and syntax

The house format for every message this gateway sends, plus the complete syntax rules
behind it: character sets, segment arithmetic, links, line breaks and the limits the
gateway enforces.

An SMS is not a string. It is 140 octets on the wire, encoded in one of two alphabets, and
which alphabet you land in is decided by a single character anywhere in the body. This page
exists so that decision is never an accident.

## Why a fixed format

A gateway SIM has no alphanumeric sender ID. Your recipients see a bare phone number, with
no app icon, no sender name and no thread context. Everything they need in order to trust
and act on the message has to be inside the 160 characters.

A fixed format buys four things:

- **Recognition.** The first token says who is writing before the reader has to guess.
- **Predictable cost.** One segment per message unless you deliberately spend more.
- **Machine-readability.** Inbound automation can parse your own outbound messages back.
- **No encoding surprises.** The format is defined inside the GSM-7 alphabet, so a message
  that follows it can never silently drop from 160 to 70 characters per segment.

## The envelope

This is a house convention, not something the gateway validates. The only body rules it
actually enforces are in [Limits the gateway enforces](#limits-the-gateway-enforces) — a
message that ignores every rule below still sends.

```
SOURCE: Headline
Detail line
key=value key=value
https://link
```

Only the first line is required. Lines are separated by a single LF (`\n`).

| Line | Required | Content |
|---|---|---|
| 1 | yes | `SOURCE: ` followed by the headline — the one sentence that matters |
| 2…n | no | Detail, one fact per line, plain prose |
| n+1 | no | Machine-readable fields, `key=value` separated by single spaces |
| last | no | Exactly one absolute `https://` URL, alone on the line |

### Line 1 — source and headline

`SOURCE` identifies the sending system: **2 to 10 characters, uppercase `A-Z` and `0-9`
only**, followed by a colon and one space.

Use a colon, not brackets. `[BACKUP]` looks tidier but `[` and `]` are GSM-7 *extension*
characters that cost two septets each, so `[BACKUP]` costs 10 septets while `BACKUP:` costs
7 — and if you ever switch the brackets to typographic ones, the whole message drops to
70 characters per segment. The format avoids the trap by construction.

The headline should read as a complete sentence and stay under roughly 100 characters.
Phones show about the first 40 characters in the lock-screen preview, so put the subject
and the verdict first: `BACKUP: failed on db-01` reads correctly when truncated,
`BACKUP: the nightly job on db-01 has…` does not.

### Detail lines

One fact per line, no bullet characters. `-` is fine if you need one (it is GSM-7 basic);
`•` and `→` are not (see [Characters that cost you](#characters-that-cost-you)).

### Data fields

For messages that a program will read back, put structured data on its own line:

```
id=8412 status=failed dur=14m free=0.4G
```

- Keys match `[a-z][a-z0-9_]*`.
- Values contain no spaces and no `=`. If a value needs a space, the message is
  human-only — drop the field line.
- Field order is not significant. Unknown keys must be ignored by parsers.

### The link line

One absolute URL, `https://`, alone on the last line, with nothing after it — not even a
full stop. See [Links in SMS](#links-in-sms).

## Worked examples

```
OSG: Gateway back online after modem reset.
```
43 characters, GSM-7, 1 segment.

```
BACKUP: Nightly backup failed on db-01.
reason=disk_full free=0.4G
https://status.example.com/j/8412
```
100 characters, GSM-7, 1 segment.

```
2FA: 481920 is your login code. Valid 5 minutes. Never share it.
```
64 characters, GSM-7, 1 segment.

```
SHOP: Order 10428 shipped, arriving Fri.
Track: https://s.example.com/t/10428
```
76 characters, GSM-7, 1 segment.

### Good and bad

| Instead of | Write | Why |
|---|---|---|
| `Hi! Your order #10428 has shipped 🎉` | `SHOP: Order 10428 shipped.` | The emoji halves the segment budget |
| `[ALERT] disk full` | `ALERT: disk full` | Brackets cost 2 septets each |
| `See https://x.example/a.` | `See https://x.example/a` (own line) | The trailing dot gets swallowed into the link |
| `Don’t reply to this number` | `Do not reply to this number` | `’` forces UCS-2 |
| `Temp: 41°C — check now…` | `Temp: 41 deg C - check now` | `°`, `—` and `…` all force UCS-2 |
| `msg from our system` | `OSG: …` | Recipients see a bare number; identify yourself |

## Character budget

An SMS carries 140 octets. How many characters fit depends on the alphabet:

| Alphabet | Single message | Each part of a concatenated message |
|---|---|---|
| GSM-7 | **160** characters | **153** |
| UCS-2 (UTF-16) | **70** characters | **67** |

Concatenated parts are shorter because 6 octets of each part are spent on the User Data
Header that tells the receiving phone how to reassemble the message.

**The alphabet is chosen per message, not per character.** One character outside GSM-7
anywhere in the body moves the entire message to UCS-2 and cuts the budget by more than
half. A 158-character message is one segment; add a single `…` and the same text becomes
three.

### GSM-7 basic characters

These cost one septet each:

```
@ £ $ ¥ è é ù ì ò Ç <LF> Ø ø <CR> Å å Δ _ Φ Γ Λ Ω Π Ψ Σ Θ Ξ Æ æ ß É
<SP> ! " # ¤ % & ' ( ) * + , - . / 0 1 2 3 4 5 6 7 8 9 : ; < = > ?
¡ A B C D E F G H I J K L M N O P Q R S T U V W X Y Z Ä Ö Ñ Ü § ¿
a b c d e f g h i j k l m n o p q r s t u v w x y z ä ö ñ ü à
```

### GSM-7 extension characters

These are still GSM-7, but each costs **two** septets — an escape plus the character:

```
form feed   €   [   ]   {   }   ^   ~   \   |
```

Ten characters of `{` cost twenty. A URL with a `~` in the path costs one extra septet.

### Characters that cost you

Everything below looks harmless in a text editor and silently forces UCS-2:

| Character | Name | GSM-7-safe replacement |
|---|---|---|
| `“ ” „ « »` | typographic and guillemet quotes | `"` |
| `‘ ’` | typographic apostrophes | `'` |
| `–` `—` | en dash, em dash | `-` |
| `…` | horizontal ellipsis | `...` |
| `•` `·` | bullet, middle dot | `-` or `*` |
| `°` | degree sign | ` deg` |
| `→` `⇒` | arrows | `->` |
| `✓` `✗` | check and cross marks | `OK`, `X` |
| `™` `©` `®` | trademark and copyright signs | drop, or spell out |
| `′` `″` | prime marks | `'`, `"` |
| U+00A0 | non-breaking space | ordinary space |
| U+200B, U+FEFF | zero-width space, BOM | delete |
| any emoji | — | delete |

Accented letters are the trap that catches people out, because some are in GSM-7 and some
are not:

| In GSM-7 | Not in GSM-7 |
|---|---|
| `à è é ì ò ù` | `á í ó ú` |
| `ä ö ü Ä Ö Ü` | `ë ï â ê î ô û` |
| `ñ Ñ ß Ç Å å Ø ø Æ æ É` | `ç` (lowercase!), `õ ã` |

Note the asymmetry: uppercase `Ç` is in the basic set, lowercase `ç` is not. A French or
Portuguese message is very likely to be UCS-2 whether you intended it or not — that is
fine, just budget 70 characters per segment instead of 160.

Two invisible characters deserve special mention: a non-breaking space pasted from a word
processor and a UTF-8 BOM at the start of a body are both invisible in every editor and
both halve your budget. If a message reports twice the segments you expected, suspect
those first.

### How the gateway counts

The gateway computes the segment count when it accepts the message and stores it on the
row. This is the exact algorithm:

```python
def count_segments(body: str) -> int:
    if all(ch in GSM7_BASIC or ch in GSM7_EXTENSION for ch in body):
        septets = sum(2 if ch in GSM7_EXTENSION else 1 for ch in body)
        return 1 if septets <= 160 else math.ceil(septets / 153)
    code_units = len(body.encode("utf-16-be")) // 2
    return 1 if code_units <= 70 else math.ceil(code_units / 67)
```

That count is an **estimate made at enqueue time**. Once the message has actually gone out,
the worker replaces it with the number of parts the modem really produced, so `segments` on
a `sent` or `delivered` message is the true count while on a `queued` one it is the
prediction. The two agree in practice; a disagreement means the modem split the text
differently than the algorithm above expected.

Two consequences worth knowing:

- UCS-2 counts **UTF-16 code units**, not characters. Every emoji above U+FFFF is a
  surrogate pair and costs two.
- The gateway counts what it will send. It never rewrites, transliterates or normalises
  your body — what you post is what is encoded.

Segment boundaries, for reference:

| Body | Segments |
|---|---|
| 160 GSM-7 characters | 1 |
| 161 GSM-7 characters | 2 |
| 306 GSM-7 characters | 2 |
| 307 GSM-7 characters | 3 |
| 70 UCS-2 characters | 1 |
| 71 UCS-2 characters | 2 |

### The live counter

The reply box in [Chats](chats.md) and the *Send test SMS* form in [Settings](settings.md)
show the character count, the detected alphabet and the segment count as you type, and
name the specific characters that pushed the message into UCS-2. It uses the same rules as
the code above.

## Links in SMS

The gateway does not shorten, rewrite, wrap or track links. What you send is delivered
byte for byte. That puts the following rules on you.

- **Always write the scheme.** `https://example.com/x`, never `example.com/x`. Many phones
  only turn a string into a tap-able link when it starts with a scheme.
- **Prefer `https://`.** Some carriers and messaging apps flag plain `http://`.
- **Put the link on its own line, last, with nothing after it.** Clients differ on where
  they end a link; a trailing full stop is frequently absorbed into the URL, and a link
  followed by more text is frequently truncated.
- **One link per message.** Two links in an SMS reads as spam to filters and to humans.
- **Keep the URL inside GSM-7.** `~ [ ] { } | \ ^` are legal in URLs and cost two septets
  each. Percent-encoding them (`%7E`) costs three characters but only three septets — a
  wash at best. The query separators `? & = + , ; : / . - _ %` are all GSM-7 basic and cost
  one septet each, so ordinary query strings are cheap.
- **No internationalised domains or non-ASCII paths.** `https://café.example/…` forces the
  whole message to UCS-2. Use punycode (`xn--caf-dma.example`) and percent-encoded paths.
- **Strip tracking parameters.** `?utm_source=sms&utm_medium=…` routinely costs 40+
  characters, which is a quarter of your segment. If you need attribution, put a short code
  in the path.
- **Budget the link first.** A 33-character link leaves 127 characters for everything else
  in a single segment. Write the link, then write the message around what is left.

### Shorteners

A shortener buys characters and costs trust: short domains are heavily abused, so some
carriers filter them and some recipients will not tap them. If you use one, use your own
domain rather than a public service — a link on a domain the recipient recognises is worth
more than the characters it saves.

## Line breaks and whitespace

- Use **LF** (`\n`). CRLF costs two septets for no benefit.
- LF is a GSM-7 basic character: one septet in GSM-7, one code unit in UCS-2.
- In JSON, that is the two-character escape `\n` inside the string:

```bash
curl -X POST http://<gateway>:8080/api/v1/messages \
  -H "Authorization: Bearer sms_..." \
  -H "Content-Type: application/json" \
  -d '{"to":"+41791234567","body":"BACKUP: failed on db-01.\nreason=disk_full\nhttps://status.example.com/j/8412"}'
```

- Never send trailing spaces or a trailing newline. They are billable and invisible.
- Blank lines between paragraphs cost a septet each and rarely survive the recipient's
  rendering intact. Prefer single line breaks.

**Whitespace handling differs by entry point**, and the difference is deliberate:

| Entry point | What happens to the body |
|---|---|
| `POST /api/v1/messages` | Sent **verbatim**. No trimming, no normalisation. |
| Chat reply box | Leading and trailing whitespace stripped; empty result rejected with 422. |
| *Send test SMS* form | Stripped; if empty, `Test from Open SMS Gateway` is sent instead. |

The chat reply box is a single-line input, so it cannot produce a multi-line body. Send
multi-line messages through the API.

## Limits the gateway enforces

| Limit | Value | On breach |
|---|---|---|
| Body length | 1600 characters | `422 validation_error` |
| Segments per message | 10 | `422 validation_error`, `body exceeds 10 SMS segments` |
| Minimum body length | 1 character | `422 validation_error` |
| Recipient format | E.164, `^\+[1-9][0-9]{6,14}$` | `422 validation_error` |

Ten segments is roughly 1530 GSM-7 or 670 UCS-2 characters — far beyond any message a
person will read, and a deliberate ceiling on what one API call can cost you and how long
it can occupy the modem. Note that both limits apply: a 700-character UCS-2 body is under
the character cap but over the segment cap, and is rejected.

Every segment also costs one token from the send throttle
(`MESSAGES_PER_MINUTE`, default 6), because operators bill and rate-limit per submitted
segment. A 5-segment message consumes five slots — the throttle stays honest.

## Recipient numbers

Always **E.164**: a `+`, a country code that does not start with zero, then 6 to 14 more
digits. No spaces, no dashes, no parentheses, no national trunk prefix.

| Input | API | Admin forms (recipients) | Gateway number field |
|---|---|---|---|
| `+41791234567` | accepted | accepted | accepted |
| `0041791234567` | **rejected** | **rejected** | accepted, converted to `+41791234567` |
| `+41 79 123 45 67` | **rejected** | **rejected** | accepted, separators stripped |
| `079 123 45 67` | rejected | rejected | rejected — no country code |

**Recipients are strict everywhere.** Both the API and the admin forms — *Send test SMS* and
the chat reply box — require exact E.164 and answer 422 otherwise. The test-SMS field also
enforces the pattern in the browser, so a malformed number is refused before it is submitted.
The reasoning is the same in both places: a number in national format is a number for the
wrong country, and silently guessing is worse than a rejection.

The one field that normalises is the **gateway number** in
[Settings](settings.md#the-gateway-number). It strips spaces, dashes, slashes and
parentheses and rewrites a leading `00` to `+`. That value is a display-only label for your
own SIM, never a recipient, which is why it can afford to be forgiving.

## Inbound messages

What you receive is what the sender's phone produced. The gateway applies no formatting
rules to inbound text and does not enforce this format on it.

- Concatenated inbound messages are reassembled from their UDH into one message before
  being stored, and `segments` records how many parts arrived.
- The sender number is whatever the network reports. It is usually E.164 with a leading
  `+`, but short codes and alphanumeric sender IDs also arrive and are stored as-is. Do
  not assume `from` matches the E.164 pattern.
- Bodies may contain anything, including emoji, newlines and control characters. Escape
  them before rendering them anywhere.
- Duplicate delivery is possible: if the gateway crashes after reading a message from the
  modem but before deleting it, the message is read again — and it is stored as a **new row
  with a new `id`**. The message `id` therefore does *not* deduplicate this case, and
  neither does `X-Gateway-Delivery`, because a fresh delivery record is created too. If
  duplicates would be harmful, deduplicate on the content itself: sender plus body plus a
  received-time window. This is a deliberate at-least-once trade — a duplicate SMS is
  recoverable, a silently dropped one is not.

If you send commands *to* the gateway number, note that the gateway itself never
interprets inbound text. Parsing is entirely your application's job.

## Etiquette and compliance

The gateway will happily let you break rules that your operator will not.

- **Identify yourself in every message.** The `SOURCE:` prefix is the format's answer to
  this, and in several jurisdictions it is a legal requirement for commercial messages.
- **Honour opt-out.** The gateway does not implement `STOP`. If you send anything that
  could be considered marketing, your application must watch inbound messages for opt-out
  keywords (`STOP`, `STOPP`, `UNSUBSCRIBE`), act on them immediately, and never message
  that number again.
- **Respect quiet hours.** Do not send non-urgent messages at night. The recipient cannot
  mute you.
- **Do not send bulk.** A consumer SIM is not a bulk channel. Operators fingerprint
  repeated identical bodies across many recipients and will filter or disconnect the SIM.
  Keep `MESSAGES_PER_MINUTE` conservative and read your operator's terms.
- **Never put secrets in an SMS** beyond short-lived one-time codes. SMS is unencrypted in
  transit, stored in plaintext on the handset, and often mirrored to laptops and watches.
- **One-time codes:** state the validity period and add "never share it". Do not include a
  link in the same message as a code — that is exactly what phishing looks like, and you
  are training your users to fall for it.
