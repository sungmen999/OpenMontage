// Split text into user-perceived characters (grapheme clusters) for
// per-letter animation.
//
// `text.split("")` splits UTF-16 code units. Once each piece sits in its own
// inline-block span, an emoji becomes two lone surrogates (each drawn as a
// U+FFFD box) and a Thai vowel or tone mark is torn from its consonant (drawn
// on a dotted circle). Both showed up in real renders on 2026-11-11.

type GraphemeSegmenter = {
  segment(input: string): Iterable<{ segment: string }>;
};
type GraphemeSegmenterCtor = new (
  locales?: string,
  options?: { granularity: "grapheme" },
) => GraphemeSegmenter;

// Code points that extend the cluster before them. Besides combining marks
// these are: Thai SARA AM and Lao AM (letters by general category, spacing
// marks for segmentation), the zero-width joiner, emoji skin tones and the
// tag characters that spell a subdivision flag.
const EXTENDS_PREVIOUS = /^(?:\p{M}|\p{Emoji_Modifier}|[ำຳ‍\u{e0020}-\u{e007f}])$/u;
const REGIONAL_INDICATOR = /^\p{Regional_Indicator}$/u;

export function splitGraphemes(text: string): string[] {
  const Segmenter = (Intl as unknown as { Segmenter?: GraphemeSegmenterCtor })
    .Segmenter;
  if (Segmenter) {
    const segmenter = new Segmenter(undefined, { granularity: "grapheme" });
    return Array.from(segmenter.segment(text), (part) => part.segment);
  }
  // No Intl.Segmenter: split by code point and keep on the previous piece
  // whatever extends it. This covers the scripts and emoji the titles see
  // (the contract test compares it with Intl.Segmenter); it is not UAX #29.
  const out: string[] = [];
  for (const codePoint of Array.from(text)) {
    const previous = out[out.length - 1];
    const joinsPrevious =
      previous !== undefined &&
      (EXTENDS_PREVIOUS.test(codePoint) ||
        previous.endsWith("‍") ||
        // A flag is two regional indicators: pair the second with the first.
        (REGIONAL_INDICATOR.test(codePoint) && REGIONAL_INDICATOR.test(previous)));
    if (joinsPrevious) {
      out[out.length - 1] += codePoint;
    } else {
      out.push(codePoint);
    }
  }
  return out;
}
