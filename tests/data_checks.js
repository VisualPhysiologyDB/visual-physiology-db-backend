const assert = require('node:assert/strict');
const {referenceHistogram, acuityHistogram, trimEmptyBins, safeLink, exportCSV} = require('../static/core/vpod-data.js');
assert.deepEqual(referenceHistogram([{year_of_publication: 2020}, {year_of_publication: 2022}, {year_of_publication: null}]), {labels: [2020,2021,2022], counts: [1,0,1], plotted: 2, excluded: 1});
assert.deepEqual(referenceHistogram([{year_of_publication: 2020, measurement_methods: ['MSP','ERG']}, {year_of_publication: 2020}]).counts, [2]);
const histogram = acuityHistogram([{cpd: .02}, {cpd: .05}, {cpd: 160}, {cpd: 200}, {cpd: null}, {cpd: 0}, {cpd: Infinity}, {cpd: NaN}]);
assert.equal(histogram.plotted, 4); assert.equal(histogram.excluded, 4);
assert.equal(histogram.counts[0], 1); assert.equal(histogram.counts[1], 1); assert.equal(histogram.counts.at(-2), 1); assert.equal(histogram.counts.at(-1), 1);
assert(histogram.counts.includes(0));
const withEmptyTails = {labels: [490, 500, 510, 520, 530], counts: [0, 1, 0, 2, 0], plotted: 3, excluded: 1};
assert.deepEqual(trimEmptyBins(withEmptyTails), {labels: [500, 510, 520], counts: [1, 0, 2], plotted: 3, excluded: 1});
assert.equal(withEmptyTails.labels.length, 5); // Display trimming does not mutate source bins.
assert.deepEqual(trimEmptyBins(acuityHistogram([{cpd: 1.5}, {cpd: null}])), {labels: ['1–<2'], counts: [1], plotted: 1, excluded: 1});
assert.deepEqual(trimEmptyBins(acuityHistogram([{cpd: null}])), {labels: [], counts: [], plotted: 0, excluded: 1});
for (const value of ['javascript:alert(1)', 'data:text/html,x', 'https://user:pass@host.test']) assert.equal(safeLink(value), null);
assert.equal(safeLink('https://doi.org/10.1234/example'), 'https://doi.org/10.1234/example');
const csv = exportCSV([{title: '=formula,"quoted"', year: 0, notes: null}]);
assert(csv.includes('"\'=formula,""quoted"""')); assert(csv.includes('"0"'));
console.log('Histogram boundaries, counts, unknowns, duplicate counting, safe URLs and CSV checks passed.');
