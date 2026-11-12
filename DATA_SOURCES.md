# Data sources and what this repository does NOT contain

The MIT licence in `LICENSE` covers the code and the documentation written for this project. It does not cover data
from the services below, and none of that data is in this repository: no database, no downloaded reviews, no
ratings, no model weights. To run the system you fetch your own copy with your own keys, under each service's terms
(see `docs/guide/REPRODUCE.md`).

| Source | Used for | Terms to read before you fetch |
| --- | --- | --- |
| TMDB API | Films, shows, people, credits, release dates, posters | This product uses the TMDB API but is not endorsed or certified by TMDB. TMDB API terms of use; attribution required. |
| MovieLens 32M (GroupLens) | Ratings for the recommendation benchmark and "people who liked this" | Research use; the data may not be redistributed. Cite: F. Maxwell Harper and Joseph A. Konstan. 2015. The MovieLens Datasets: History and Context. ACM TiiS 5(4). |
| The Guardian Open Platform | Reviews and articles about films (links and short summaries) | Guardian Open Platform terms; show "From the Guardian" and link to the article. |
| The New York Times API | Review links | NYT API terms. |
| Wikipedia / Wikidata | Reception text, identifiers | Wikipedia text: CC BY-SA 4.0 (attribution, share-alike). Wikidata: CC0. |
| OMDb API | IMDb rating and votes | OMDb terms (patron key). |
| YouTube Data API | Review and essay videos about films | YouTube API Services terms; stored data refreshed or deleted within 30 days. |
| JustWatch (via TMDB) | Where to watch | Attribution to JustWatch where listings are shown. |
| Trakt API | A person's own watch history, when they connect it | Trakt API terms. |

Test sets that were used once for a final, sealed evaluation are not included, so the published numbers can be
read but those exact tests cannot be re-run from this copy. Everything else in `docs/guide/EVALUATION_AND_RESULTS.md`
says how each number was measured.
