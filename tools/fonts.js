// Serves Montserrat and Libre Caslon Text from local @fontsource copies in place of Google Fonts (the container
// cannot reach Google), so screenshots show the real type.
const fs = require('fs'); const path = require('path');
const M = path.join(__dirname, 'node_modules/@fontsource/montserrat/files'), C = path.join(__dirname, 'node_modules/@fontsource/libre-caslon-text/files');
const faces = [...[400, 500, 600, 700].map(w => ['Montserrat', w, 'normal', `${M}/montserrat-latin-${w}-normal.woff2`]),
  ['Libre Caslon Text', 400, 'normal', `${C}/libre-caslon-text-latin-400-normal.woff2`], ['Libre Caslon Text', 700, 'normal', `${C}/libre-caslon-text-latin-700-normal.woff2`],
  ['Libre Caslon Text', 400, 'italic', `${C}/libre-caslon-text-latin-400-italic.woff2`]];
const css = faces.map(([f, w, st, file], i) => `@font-face{font-family:'${f}';font-weight:${w};font-style:${st};font-display:swap;src:url(https://fonts.gstatic.com/local/${i}.woff2) format('woff2');}`).join('\n');
module.exports = async (ctx) => {
  await ctx.route(/fonts\.googleapis\.com/, r => r.fulfill({ contentType: 'text/css', body: css }));
  await ctx.route(/fonts\.gstatic\.com\/local\/(\d+)\.woff2/, r => { const i = +r.request().url().match(/local\/(\d+)/)[1]; r.fulfill({ contentType: 'font/woff2', body: fs.readFileSync(faces[i][3]) }); });
};
