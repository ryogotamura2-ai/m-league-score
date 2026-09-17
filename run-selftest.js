#!/usr/bin/env node
/* ============================================================================
   index.html から計算のしんぶと自己点検だけを取り出し、画面なしで走らせる。
   ブラウザで動いているのとまったく同じコードを実行するので、二重管理にならない。

     node run-selftest.js

   1件でも落ちたら終了コード 1 を返す。
   ============================================================================ */
'use strict';

var fs = require('fs');
var path = require('path');
var vm = require('vm');

var file = path.join(__dirname, 'index.html');
var html = fs.readFileSync(file, 'utf8');

/* **焼き込んだデータを先に切り落とす。**
   ページには取得してきた名前が JSON として埋まっている。以前はここを
   「属性なしの <script> だけ拾うから混ざらない」と考えていたが、**間違いだった。**
   名前に `<script>` という並びが入っていると、下の正規表現がそこから拾い始め、
   データの閉じタグまでをコードとして実行してしまう。第三者のデータベースから
   取ってきた名前で、任意のコマンドを実行できることを確かめた。
   焼き込み側でも文字を潰したが、**読む側でも同じ穴を塞いでおく。** */
var DATA_OPEN = '<script type="application/json" id="ml-data">';
var dataStart = html.indexOf(DATA_OPEN);
if (dataStart >= 0) {
  var dataEnd = html.indexOf('<\/script>', dataStart);
  if (dataEnd < 0) {
    console.error('index.html のデータ枠が閉じていません');
    process.exit(2);
  }
  html = html.slice(0, dataStart) + html.slice(dataEnd);
}

/* 行頭の <script> だけを目印にする（このページのタグは全部行頭にある）。 */
var blocks = [];
var re = /^<script>\r?\n([\s\S]*?)^<\/script>/gm, m;
while ((m = re.exec(html)) !== null) blocks.push(m[1]);

if (blocks.length < 2) {
  console.error('index.html から script ブロックを取り出せませんでした（見つかった数: ' + blocks.length + '）');
  process.exit(2);
}

/* **この文脈では走らせない。** runInThisContext だと process が見えるので、
   万一また取り出しを誤ったときに被害が青天井になる。別の文脈で走らせれば、
   取り出しを誤っても外へ手が届かない。 */
var sandbox = { console: console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

try {
  vm.runInContext(blocks[0], sandbox, { filename: 'index.html#core' });
  vm.runInContext(blocks[1], sandbox, { filename: 'index.html#selftest' });
} catch (e) {
  console.error('コードの読み込みで失敗しました: ' + e.stack);
  process.exit(2);
}

if (typeof sandbox.SelfTest === 'undefined') {
  console.error('index.html から SelfTest を取り出せませんでした');
  process.exit(2);
}

var t0 = Date.now();
var results = sandbox.SelfTest.run();
var ms = Date.now() - t0;

/* 検査が0件なら「全部合格」ではなく失敗にする。取り出しがおかしいのに
   「0件中0件 合格」で素通りすると、壊れていることに誰も気づけない。 */
if (!Array.isArray(results) || results.length === 0) {
  console.error('自己点検が1件も返ってきませんでした（取り出しがおかしい可能性）');
  process.exit(2);
}

var pass = 0;
results.forEach(function (r, i) {
  if (r.ok) pass++;
  console.log((r.ok ? '合格  ' : '不合格') + ' ' + String(i + 1).padStart(2, ' ') + '. ' + r.name);
  console.log('        ' + r.detail);
});
console.log('');
console.log(results.length + '件中 ' + pass + '件 合格（' + ms + 'ミリ秒）');

process.exit(pass === results.length ? 0 : 1);
