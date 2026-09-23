# TMDB "tone" keywords describe a mood, not a plot. They dilute the vector.
TONE = {"hilarious","baffled","callous","dubious","farcical","mean spirited","straightforward",
        "urgent","critical","egotistical","complex","intense","desperate","satirical",
        "admiring","amused","angry","anxious","awestruck","bold","cautionary","compassionate",
        "defiant","dramatic","earnest","enchant","excited","exhilarated","frustrated","hopeful",
        "horrified","inspirational","melancholy","nostalgic","playful","provocative","romantic",
        "sad","suspenseful","sympathetic","tense","thoughtful","witty","absurd","aggressive",
        "ambiguous","cheerful","clinical","comforting","depressing","familiar","grim","harsh",
        "informative","joyful","loving","powerful","reflective","sincere","vibrant","whimsical"}

def clean_keywords(kw_string, limit=20):
    kws = [k.strip() for k in (kw_string or "").split(",") if k.strip()]
    kept = [k for k in dict.fromkeys(kws) if k.lower() not in TONE]   # also drops duplicates
    return ", ".join(kept[:limit])
