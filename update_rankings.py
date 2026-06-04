<!--
  ALL MO Sports — Homepage Rankings Snapshot Widget
  ==================================================
  HOW TO INSTALL:
    1. On your WordPress homepage add a Custom HTML block
    2. Paste this entire file into it
    3. Save/Update the page
-->
 
<style>
#ams-snapshot *{box-sizing:border-box;margin:0;padding:0}
#ams-snapshot{font-family:inherit;padding:1.5rem 0}
.ams-snap-header{margin-bottom:1.25rem}
.ams-snap-title{font-size:20px;font-weight:600;color:#0f172a;margin-bottom:4px}
.ams-snap-sub{font-size:14px;color:#64748b}
.ams-snap-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.ams-snap-card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem 1.1rem;display:flex;flex-direction:column}
.ams-snap-card-head{display:flex;align-items:center;gap:8px;margin-bottom:10px}
.ams-snap-badge{width:30px;height:30px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}
.ams-snap-sport-name{font-size:14px;font-weight:600;color:#0f172a;line-height:1.2}
.ams-snap-season{font-size:11px;color:#94a3b8}
.ams-snap-divider{border:none;border-top:1px solid #f1f5f9;margin:0 0 8px}
.ams-snap-list{list-style:none;flex:1}
.ams-snap-row{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #f8fafc}
.ams-snap-row:last-child{border-bottom:none}
.ams-snap-rank{font-size:11px;color:#94a3b8;min-width:18px;text-align:right}
.ams-snap-school{font-size:13px;color:#1e293b;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ams-snap-ovr{font-size:12px;font-weight:600;color:#2563eb;min-width:36px;text-align:right}
.ams-snap-footer{margin-top:10px;padding-top:8px;border-top:1px solid #f1f5f9}
.ams-snap-link{font-size:12px;color:#64748b;text-decoration:none}
.ams-snap-link:hover{color:#2563eb;text-decoration:underline}
.ams-snap-updated{font-size:11px;color:#94a3b8;margin-top:12px}
.ams-snap-error{font-size:12px;color:#94a3b8;font-style:italic}
.ams-snap-skeleton{background:#f1f5f9;border-radius:4px;display:inline-block;animation:ams-pulse 1.4s ease-in-out infinite}
@keyframes ams-pulse{0%,100%{opacity:1}50%{opacity:.5}}
.ams-snap-skel-row{display:flex;align-items:center;gap:8px;padding:5px 0}
@media(max-width:600px){.ams-snap-grid{grid-template-columns:1fr}}
</style>
 
<div id="ams-snapshot">
  <div class="ams-snap-header">
    <div class="ams-snap-title">Current rankings snapshot</div>
    <div class="ams-snap-sub">Top 5 teams across all active Missouri high school sports</div>
  </div>
  <div class="ams-snap-grid" id="ams-snap-grid"></div>
  <div class="ams-snap-updated" id="ams-snap-updated"></div>
</div>
 
<script>
(function(){
 
  var JSON_URL = 'https://raw.githubusercontent.com/AllMOSports/All_MO_Sports-Data/main/rankings.json';
 
  /* Display order of sport cards — all 9 sports */
  var ORDER = ['FTB','BBB','GBB','BSB','GSC','SFT','BSC','GVB','FST'];
 
  var grid = document.getElementById('ams-snap-grid');
 
  /* ── RENDER HELPERS ─────────────────────────────────────── */
 
  function cardHeader(abbr, sport) {
    return '<div class="ams-snap-card-head">' +
      '<div class="ams-snap-badge" style="background:' + sport.badgeBg + ';color:' + sport.badgeFg + '">' +
        abbr +
      '</div>' +
      '<div>' +
        '<div class="ams-snap-sport-name">' + sport.name + '</div>' +
        '<div class="ams-snap-season">' + sport.season + '</div>' +
      '</div>' +
    '</div>';
  }
 
  function skeletonCard(abbr) {
    var rows = '';
    for (var i = 0; i < 5; i++) {
      rows += '<li class="ams-snap-skel-row">' +
        '<span class="ams-snap-skeleton" style="width:14px;height:10px"></span>' +
        '<span class="ams-snap-skeleton" style="flex:1;height:10px"></span>' +
        '<span class="ams-snap-skeleton" style="width:30px;height:10px"></span>' +
      '</li>';
    }
    var card = document.createElement('div');
    card.className = 'ams-snap-card';
    card.id = 'ams-card-' + abbr;
    card.innerHTML =
      '<div style="height:38px;display:flex;align-items:center;gap:8px;margin-bottom:10px">' +
        '<span class="ams-snap-skeleton" style="width:30px;height:30px;border-radius:6px"></span>' +
        '<div>' +
          '<span class="ams-snap-skeleton" style="width:110px;height:12px;display:block;margin-bottom:4px"></span>' +
          '<span class="ams-snap-skeleton" style="width:70px;height:9px;display:block"></span>' +
        '</div>' +
      '</div>' +
      '<hr class="ams-snap-divider">' +
      '<ul class="ams-snap-list">' + rows + '</ul>';
    return card;
  }
 
  function renderCard(abbr, sport) {
    var card = document.getElementById('ams-card-' + abbr);
    if (!card) return;
 
    var rows = '';
    if (!sport.teams || sport.teams.length === 0) {
      rows = '<li class="ams-snap-row"><span class="ams-snap-error">Season not yet started</span></li>';
    } else {
      sport.teams.forEach(function(t) {
        rows += '<li class="ams-snap-row">' +
          '<span class="ams-snap-rank">' + t.rank + '</span>' +
          '<span class="ams-snap-school">' + t.school + '</span>' +
          '<span class="ams-snap-ovr">' + t.ovr + '</span>' +
        '</li>';
      });
    }
 
    card.innerHTML =
      cardHeader(abbr, sport) +
      '<hr class="ams-snap-divider">' +
      '<ul class="ams-snap-list">' + rows + '</ul>' +
      '<div class="ams-snap-footer">' +
        '<a class="ams-snap-link" href="' + sport.url + '">View full rankings &rarr;</a>' +
      '</div>';
  }
 
  /* ── INIT ───────────────────────────────────────────────── */
 
  ORDER.forEach(function(abbr) {
    grid.appendChild(skeletonCard(abbr));
  });
 
  fetch(JSON_URL)
    .then(function(res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then(function(data) {
      ORDER.forEach(function(abbr) {
        if (data[abbr]) renderCard(abbr, data[abbr]);
      });
      if (data.updated) {
        document.getElementById('ams-snap-updated').textContent =
          'Rankings last updated ' + data.updated;
      }
    })
    .catch(function(err) {
      console.warn('[ALL MO Sports] Could not load rankings:', err);
      ORDER.forEach(function(abbr) {
        var card = document.getElementById('ams-card-' + abbr);
        if (card) card.innerHTML =
          '<div class="ams-snap-error" style="padding:1rem">Could not load rankings. Please try again later.</div>';
      });
    });
 
})();
</script>
