// Serves Archivo and Source Sans 3 from local @fontsource copies in place of Google Fonts (the container
// cannot reach Google), so screenshots show the real type.
const fs = require('fs'); const path = require('path');
const A = path.join(__dirname, 'node_modules/@fontsource/archivo/files'), SS = path.join(__dirname, 'node_modules/@fontsource/source-sans-3/files');
const faces = [...[500, 600, 700].map(w => ['Archivo', w, 'normal', `${A}/archivo-latin-${w}-normal.woff2`]),
  ...[400, 600, 700].map(w => ['Source Sans 3', w, 'normal', `${SS}/source-sans-3-latin-${w}-normal.woff2`]),
  ['Source Sans 3', 400, 'italic', `${SS}/source-sans-3-latin-400-italic.woff2`]];
const css = faces.map(([f, w, st, file], i) => `@font-face{font-family:'${f}';font-weight:${w};font-style:${st};font-display:swap;src:url(https://fonts.gstatic.com/local/${i}.woff2) format('woff2');}`).join('\n');
module.exports = async (ctx) => {
  await ctx.route(/fonts\.googleapis\.com/, r => r.fulfill({ contentType: 'text/css', body: css }));
  await ctx.route(/fonts\.gstatic\.com\/local\/(\d+)\.woff2/, r => { const i = +r.request().url().match(/local\/(\d+)/)[1]; r.fulfill({ contentType: 'font/woff2', body: fs.readFileSync(faces[i][3]) }); });
};
