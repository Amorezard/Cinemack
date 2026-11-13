#!/usr/bin/env python3
"""Validate only the supplied Cinemack planning kit: schemas and fictional samples.

No repo access, server access, network calls, secrets, DB writes or model inference.
Requires the optional `jsonschema` Python package to validate samples against schemas.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / 'specs'
EXAMPLES = ROOT / 'examples'
PAIRS = [
    ('movie_query.schema.json', 'example_movie_query.json'),
    ('prediction_response.schema.json', 'example_prediction_response_MOCK_ONLY.json'),
    ('recommendation_event.schema.json', 'example_recommendation_event.json'),
    ('deal_offer.schema.json', 'example_deal_offer_MOCK_ONLY.json'),
]


def main():
    try:
        import jsonschema
    except ImportError as e:
        raise SystemExit('Validation dependency missing: pip install jsonschema') from e
    print('Cinemack proposed schema kit validation (NOT a Cinemack application test)')
    for schema_file, example_file in PAIRS:
        schema = json.loads((SCHEMAS / schema_file).read_text(encoding='utf-8'))
        example = json.loads((EXAMPLES / example_file).read_text(encoding='utf-8'))
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
        errors = sorted(validator.iter_errors(example), key=lambda e: str(e.path))
        if errors:
            raise ValueError(f'{example_file}: ' + '; '.join(e.message for e in errors))
        print(f'  PASS {example_file} matches {schema_file}')

    # Additional toy-example invariants: do NOT consider these comprehensive business validation.
    forecast = json.loads((EXAMPLES/'example_prediction_response_MOCK_ONLY.json').read_text())
    dom = forecast['domestic_gross']['point']
    intl = forecast['international_gross']['point']
    world = forecast['worldwide_gross']['point']
    assert dom + intl == world, 'Example forecast does not reconcile'
    budget = forecast['budget']['amount']
    assert budget > 0 and abs(forecast['gross_to_budget_multiple']['point'] - world/budget) < 1e-8
    for k in ('domestic_gross','international_gross','worldwide_gross','gross_to_budget_multiple'):
        item = forecast[k]
        intervals = sorted(item['intervals'], key=lambda x: x['nominal_coverage'])
        for interval in intervals:
            assert interval['lower'] >= 0 and interval['upper'] >= interval['lower']
            assert interval['lower'] <= item['point'] <= interval['upper']
        if len(intervals) >= 2:
            assert intervals[0]['lower'] >= intervals[1]['lower']
            assert intervals[0]['upper'] <= intervals[1]['upper']
    print('  PASS illustrative forecast arithmetical invariants')
    print('All kit fixtures validated. These invented examples are NOT real movie or deal data.')

if __name__ == '__main__':
    main()
