# Rejected: min-max score normalisation across retrievers

Normalising each retriever's scores into a common range and adding them was
considered as the fusion rule and **rejected** in favour of rank fusion.

## The proposal

Take each retriever's raw score, map it onto `[0, 1]` with a min-max transform
over the result set it returned, then sum the mapped values per document. One
tunable weight per retriever, one number per document, one sort.

## Why it was rejected

- **The scores are not comparable, and normalising does not make them so.** A
  BM25 score is a function of the collection statistics of the index it came
  from; a vector similarity is a cosine. Mapping both onto the same interval
  produces two numbers of the same type and not of the same meaning.
- **The transform depends on the result set.** Min-max over the returned window
  means a document's normalised score changes when a document it has never
  interacted with enters or leaves the window. Adding a row at rank fifty moves
  the score at rank one.
- **The collection statistics leak.** A normalised BM25 score is computed over
  every row in the index, including the rows a caller may not read. Publishing it
  publishes a function of content that was withheld.
- **Tuning has no ground truth.** The weights would be chosen by looking at a
  handful of queries, and there was no evaluation corpus to choose them against.

## What was adopted instead

Reciprocal rank fusion. Only the ranks fuse, each retriever keeps its own scoring
rule, and the fused value is a function of positions rather than of magnitudes.
