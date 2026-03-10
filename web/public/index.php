<?php
/**
 * Ledger Review UI
 * Review beancount entries alongside their source documents.
 */

$ledgerBase = realpath(__DIR__ . '/../../ledger');

// Serve PDF files from ledger folders
if (isset($_GET['pdf'])) {
    $requested = realpath($ledgerBase . '/' . $_GET['pdf']);
    if ($requested && str_starts_with($requested, $ledgerBase) && str_ends_with($requested, '.pdf') && is_file($requested)) {
        header('Content-Type: application/pdf');
        header('Content-Length: ' . filesize($requested));
        readfile($requested);
        exit;
    }
    http_response_code(404);
    exit('Not found');
}

// API: return single event's beancount entries (lazy-loaded)
if (isset($_GET['api']) && $_GET['api'] === 'entry' && isset($_GET['folder'])) {
    $folder = $_GET['folder'];
    if (!preg_match('#^20\d{2}/[a-z0-9-]+$#', $folder)) {
        http_response_code(400);
        exit(json_encode(['error' => 'Invalid folder']));
    }
    $file = $ledgerBase . '/' . $folder . '/entries.beancount';
    $realFile = realpath($file);
    if ($realFile && str_starts_with($realFile, $ledgerBase) && is_file($realFile)) {
        header('Content-Type: application/json');
        echo json_encode(['entries' => file_get_contents($realFile)], JSON_UNESCAPED_UNICODE);
    } else {
        http_response_code(404);
        echo json_encode(['error' => 'Not found']);
    }
    exit;
}

// API: return events as JSON (metadata only - entries loaded lazily)
if (isset($_GET['api']) && $_GET['api'] === 'events') {
    $events = scanLedger($ledgerBase);
    header('Content-Type: application/json');
    echo json_encode($events, JSON_UNESCAPED_UNICODE);
    exit;
}

// Shared: parse bean-query CSV output into [{account, currency, amount}]
function parseBeanCsv(string $csv): array {
    $lines = explode("\n", trim($csv));
    if (count($lines) < 2) return [];
    $rows = [];
    for ($i = 1; $i < count($lines); $i++) {
        $parts = str_getcsv($lines[$i]);
        if (count($parts) < 3) continue;
        $amount = floatval(trim($parts[2]));
        if (abs($amount) < 0.005) continue;
        $rows[] = [
            'account' => trim($parts[0]),
            'currency' => trim($parts[1]),
            'amount' => $amount,
        ];
    }
    return $rows;
}

// API: FX rates - latest rate at or before year-end for each currency
if (isset($_GET['api']) && $_GET['api'] === 'fxrates') {
    $pricesFile = realpath(__DIR__ . '/../../ledger/prices.beancount');
    $rates = []; // {currency => [{date, rate}]}
    foreach (file($pricesFile) as $line) {
        if (preg_match('/^(\d{4}-\d{2}-\d{2})\s+price\s+(\w+)\s+([\d.]+)\s+USD/', $line, $m)) {
            $rates[$m[2]][] = ['date' => $m[1], 'rate' => floatval($m[3])];
        }
    }
    // For each year 2018-2026, find latest rate at or before Dec 31
    $result = [];
    for ($y = 2018; $y <= 2026; $y++) {
        $yearEnd = $y . '-12-31';
        $yearRates = ['USD' => 1.0];
        foreach ($rates as $cur => $entries) {
            $best = null;
            foreach ($entries as $e) {
                if ($e['date'] <= $yearEnd && ($best === null || $e['date'] > $best['date'])) {
                    $best = $e;
                }
            }
            if ($best) $yearRates[$cur] = $best['rate'];
        }
        $result[$y] = $yearRates;
    }
    header('Content-Type: application/json');
    echo json_encode($result);
    exit;
}

// API: P&L for a given year
if (isset($_GET['api']) && $_GET['api'] === 'pnl' && isset($_GET['year'])) {
    $year = intval($_GET['year']);
    if ($year < 2018 || $year > 2030) { http_response_code(400); exit(json_encode(['error' => 'Invalid year'])); }
    $beanQuery = realpath(__DIR__ . '/../../.venv/bin/bean-query');
    $mainFile = realpath(__DIR__ . '/../../ledger/main.beancount');
    $query = "SELECT account, currency, sum(number) WHERE (account ~ '^Income' OR account ~ '^Expenses') AND year = {$year} GROUP BY account, currency ORDER BY account, currency";
    $csv = shell_exec(escapeshellarg($beanQuery) . ' ' . escapeshellarg($mainFile) . ' ' . escapeshellarg($query) . ' --format csv 2>&1');
    header('Content-Type: application/json');
    echo json_encode(parseBeanCsv($csv));
    exit;
}

// API: Account journal - all postings for a given account with running balance
if (isset($_GET['api']) && $_GET['api'] === 'account' && isset($_GET['name'])) {
    $account = $_GET['name'];
    if (!preg_match('/^(Assets|Liabilities|Equity|Income|Expenses)(:[A-Za-z0-9_-]+)+$/', $account)) {
        http_response_code(400);
        exit(json_encode(['error' => 'Invalid account name']));
    }
    $beanQuery = realpath(__DIR__ . '/../../.venv/bin/bean-query');
    $mainFile = realpath(__DIR__ . '/../../ledger/main.beancount');
    $query = "SELECT date, narration, number, currency, balance, filename WHERE account = '{$account}' ORDER BY date";
    $csv = shell_exec(escapeshellarg($beanQuery) . ' ' . escapeshellarg($mainFile) . ' ' . escapeshellarg($query) . ' --format csv 2>&1');
    $lines = explode("\n", trim($csv));
    $rows = [];
    for ($i = 1; $i < count($lines); $i++) {
        $parts = str_getcsv($lines[$i]);
        if (count($parts) < 6) continue;
        if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', trim($parts[0]))) continue;
        // balance field is like "  6403.00 USD" or "  100.00 EUR, 200.00 USD"
        $balStr = trim($parts[4]);
        // filename - strip ledger base path to get relative folder
        $fn = trim($parts[5]);
        $source = str_replace($ledgerBase . '/', '', $fn);
        $source = str_replace('/entries.beancount', '', $source);
        $balParts = [];
        foreach (preg_split('/,\s*/', $balStr) as $bp) {
            if (preg_match('/([-\d.,]+)\s+([A-Z]{3})/', $bp, $bm)) {
                $balParts[] = ['amount' => floatval(str_replace(',', '', $bm[1])), 'currency' => $bm[2]];
            }
        }
        $rows[] = [
            'date' => trim($parts[0]),
            'narration' => trim($parts[1]),
            'amount' => floatval(trim($parts[2])),
            'currency' => trim($parts[3]),
            'balance' => $balParts,
            'source' => $source,
        ];
    }
    header('Content-Type: application/json');
    echo json_encode($rows);
    exit;
}

// API: Trial Balance - all accounts with non-zero balances
if (isset($_GET['api']) && $_GET['api'] === 'trialbal') {
    $beanQuery = realpath(__DIR__ . '/../../.venv/bin/bean-query');
    $mainFile = realpath(__DIR__ . '/../../ledger/main.beancount');
    $query = "SELECT account, currency, sum(number) GROUP BY account, currency ORDER BY account, currency";
    $csv = shell_exec(escapeshellarg($beanQuery) . ' ' . escapeshellarg($mainFile) . ' ' . escapeshellarg($query) . ' --format csv 2>&1');
    header('Content-Type: application/json');
    echo json_encode(parseBeanCsv($csv));
    exit;
}

// API: Balance Sheet cumulative through a given year
if (isset($_GET['api']) && $_GET['api'] === 'balsheet' && isset($_GET['year'])) {
    $year = intval($_GET['year']);
    if ($year < 2018 || $year > 2030) { http_response_code(400); exit(json_encode(['error' => 'Invalid year'])); }
    $beanQuery = realpath(__DIR__ . '/../../.venv/bin/bean-query');
    $mainFile = realpath(__DIR__ . '/../../ledger/main.beancount');
    $query = "SELECT account, currency, sum(number) WHERE (account ~ '^Assets' OR account ~ '^Liabilities' OR account ~ '^Equity') AND year <= {$year} GROUP BY account, currency ORDER BY account, currency";
    $csv = shell_exec(escapeshellarg($beanQuery) . ' ' . escapeshellarg($mainFile) . ' ' . escapeshellarg($query) . ' --format csv 2>&1');
    header('Content-Type: application/json');
    echo json_encode(parseBeanCsv($csv));
    exit;
}

function scanLedger(string $base): array {
    $events = [];
    $years = glob($base . '/20*', GLOB_ONLYDIR);
    sort($years);
    foreach ($years as $yearDir) {
        $folders = glob($yearDir . '/*/entries.beancount');
        foreach ($folders as $beancountFile) {
            $folder = dirname($beancountFile);
            $relFolder = str_replace($base . '/', '', $folder);
            $folderName = basename($folder);

            $entries = file_get_contents($beancountFile);

            // Date from first transaction in the file
            preg_match('/^(\d{4}-\d{2}-\d{2})\s+\*/m', $entries, $dateMatch);
            $date = $dateMatch[1] ?? substr($folderName, 0, 10);

            $pdfs = glob($folder . '/*.pdf');
            $pdfPaths = array_map(fn($p) => str_replace($base . '/', '', $p), $pdfs);

            preg_match_all('/\^([\w-]+)/', $entries, $linkMatches);
            $links = array_values(array_unique($linkMatches[1] ?? []));

            preg_match('/\d{4}-\d{2}-\d{2}\s+\*\s+"([^"]+)"/', $entries, $titleMatch);
            $title = $titleMatch[1] ?? $folderName;

            preg_match_all('/#([\w-]+)/', $entries, $tagMatches);
            $tags = array_values(array_unique($tagMatches[1] ?? []));

            // Reconciled: balance assertion at exactly zero (not 10.00, 200.00 etc.)
            $reconciled = (bool)preg_match('/^\d{4}-\d{2}-\d{2}\s+balance\s+\S+\s+0(\.0+)?\s+[A-Z]{3}/m', $entries);
            $year = basename($yearDir);

            $events[] = [
                'folder' => $folderName,
                'relFolder' => $relFolder,
                'year' => $year,
                'date' => $date,
                'title' => $title,
                'pdfs' => $pdfPaths,
                'links' => $links,
                'tags' => $tags,
                'reconciled' => $reconciled,
            ];
        }
    }
    // Sort by year desc, then date desc within year
    usort($events, fn($a, $b) => strcmp($b['year'], $a['year']) ?: strcmp($b['date'], $a['date']));
    return $events;
}
?>
<!DOCTYPE html>
<html lang="en" class="h-full">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ledger Review</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: { mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'] },
      colors: {
        surface: '#161b22',
        border: '#30363d',
      }
    }
  }
}
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  [x-cloak] { display: none !important; }
  .spinner { border: 2px solid #30363d; border-top-color: #3b82f6; border-radius: 50%; width: 20px; height: 20px; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .report-row { display: grid; grid-template-columns: minmax(20rem, 1fr) repeat(calc(var(--cols) - 1), 8rem); }
</style>
<script defer src="https://cdn.jsdelivr.net/npm/@alpinejs/collapse@3.x.x/dist/cdn.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
</head>
<body class="h-full bg-gray-950 text-gray-200 font-mono text-sm dark" style="font-size:15px">

<div x-data="ledgerApp()" x-init="init()" class="flex flex-col h-full" x-cloak>

  <!-- Loading state -->
  <template x-if="loading">
    <div class="flex items-center justify-center h-full gap-3">
      <div class="spinner"></div>
      <span class="text-gray-500 text-sm">Loading ledger...</span>
    </div>
  </template>

  <!-- Error state -->
  <template x-if="error">
    <div class="flex items-center justify-center h-full">
      <span class="text-red-400 text-sm" x-text="error"></span>
    </div>
  </template>

  <template x-if="!loading && !error">
    <div class="flex flex-col h-full">

  <!-- Header -->
  <header class="flex items-center gap-3 px-4 py-2.5 bg-surface border-b border-border shrink-0">
    <h1 class="text-blue-400 font-semibold text-sm">Ledger Review</h1>
    <div class="flex gap-1 ml-2">
      <template x-for="p in [{id:'ledger',label:'Ledger'},{id:'pnl',label:'P&L'},{id:'balsheet',label:'Balance Sheet'},{id:'trialbal',label:'Trial Balance'}]" :key="p.id">
        <button
          @click="switchPage(p.id)"
          :class="page === p.id ? 'bg-gray-700 text-gray-200' : 'text-gray-500 hover:text-gray-300'"
          class="px-2.5 py-1 rounded text-xs transition-colors"
          x-text="p.label"
        ></button>
      </template>
    </div>
    <span x-show="page === 'ledger'" class="text-gray-500 text-xs" x-text="filteredEvents.length + ' events'"></span>
    <div class="flex-1"></div>
    <div x-show="page === 'ledger'" class="relative">
      <input
        x-model.debounce.200ms="search"
        x-ref="searchInput"
        type="text"
        placeholder="Search... (/)"
        class="bg-gray-800 border border-border rounded px-2.5 py-1 text-xs text-gray-200 w-48 focus:w-64 transition-all focus:outline-none focus:border-blue-500 placeholder-gray-600"
      >
      <button x-show="search" @click="search = ''" class="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-xs">&times;</button>
    </div>
    <span x-show="page === 'ledger'" class="text-gray-600 text-xs">j/k nav</span>
  </header>

  <!-- Account detail modal -->
  <div x-show="acctModal" class="fixed inset-0 z-50 flex items-start justify-center pt-12" @keydown.escape.window="acctModal = false">
    <div class="absolute inset-0 bg-black/60" @click="acctModal = false"></div>
    <div class="relative bg-gray-900 border border-border rounded-lg shadow-2xl w-[90vw] max-w-5xl h-[80vh] flex flex-col">
      <!-- Modal header -->
      <div class="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div>
          <h2 class="text-sm font-semibold text-purple-400" x-text="acctName"></h2>
          <div class="text-[11px] text-gray-500 mt-0.5" x-text="acctJournal.length + ' postings'"></div>
        </div>
        <button @click="acctModal = false" class="text-gray-500 hover:text-gray-300 text-lg px-2">&times;</button>
      </div>
      <!-- Loading -->
      <div x-show="acctLoading" class="flex-1 flex items-center justify-center py-12">
        <div class="spinner"></div>
      </div>
      <!-- Journal table -->
      <div x-show="!acctLoading" class="flex-1 overflow-auto">
        <table class="w-full text-xs">
          <thead class="sticky top-0 bg-gray-900 z-10">
            <tr class="border-b border-border text-gray-500">
              <th class="text-left py-2 px-3 font-normal w-24">Date</th>
              <th class="text-left py-2 px-3 font-normal">Description</th>
              <th class="text-right py-2 px-3 font-normal w-28">Debit</th>
              <th class="text-right py-2 px-3 font-normal w-28">Credit</th>
              <th class="text-right py-2 px-3 font-normal w-32">Balance</th>
              <th class="text-left py-2 px-3 font-normal">Source</th>
            </tr>
          </thead>
          <tbody>
            <template x-for="(row, i) in acctJournal" :key="i">
              <tr class="border-b border-border/50 hover:bg-gray-800/30">
                <td class="py-1.5 px-3 text-green-400 whitespace-nowrap" x-text="row.date"></td>
                <td class="py-1.5 px-3 text-gray-300 truncate max-w-md" :title="row.narration" x-text="row.narration"></td>
                <td class="py-1.5 px-3 text-right whitespace-nowrap"
                    :class="row.amount > 0 ? 'text-gray-300' : 'text-transparent'"
                    x-text="row.amount > 0 ? fmtAmount(row.amount, row.currency) + ' ' + row.currency : ''"></td>
                <td class="py-1.5 px-3 text-right whitespace-nowrap"
                    :class="row.amount < 0 ? 'text-red-400' : 'text-transparent'"
                    x-text="row.amount < 0 ? fmtAmount(-row.amount, row.currency) + ' ' + row.currency : ''"></td>
                <td class="py-1.5 px-3 text-right whitespace-nowrap text-yellow-600"
                    x-text="row.balance.filter(b => b.currency === row.currency).map(b => fmtAmount(b.amount, b.currency) + ' ' + b.currency).join(', ')"></td>
                <td class="py-1.5 px-3 text-gray-500 truncate max-w-xs text-xs" :title="row.source" x-text="row.source"></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Report pages: P&L and Balance Sheet -->
  <template x-if="page === 'pnl' || page === 'balsheet'">
    <div class="flex flex-col flex-1 overflow-hidden">
      <!-- Year tabs -->
      <div class="flex gap-1 px-4 py-2 bg-surface border-b border-border shrink-0">
        <template x-for="y in reportYears" :key="y">
          <button
            @click="selectReportYear(y)"
            :class="reportYear === y ? 'bg-gray-700 text-gray-200' : 'text-gray-500 hover:text-gray-300'"
            class="px-2.5 py-1 rounded text-xs transition-colors"
            x-text="y"
          ></button>
        </template>
      </div>
      <!-- Report loading -->
      <div x-show="reportLoading" class="flex-1 flex items-center justify-center">
        <div class="spinner"></div>
      </div>
      <!-- Report content -->
      <div x-show="!reportLoading" class="flex-1 overflow-auto p-4">
        <div x-show="reportData.length === 0" class="text-gray-500 text-center mt-8">No data for this year.</div>
        <template x-if="reportData.length > 0">
          <div>
            <!-- Report rendered as div grid for clean Alpine iteration -->
            <div class="report-grid" :style="'--cols:' + (reportCurrencies.length + 2)">
              <!-- Header -->
              <div class="report-row border-b border-border">
                <div class="py-2 px-2 text-gray-500 text-xs">Account</div>
                <template x-for="cur in reportCurrencies" :key="'h-'+cur">
                  <div class="text-right py-2 px-2 text-gray-500 text-xs" x-text="cur"></div>
                </template>
                <div class="text-right py-2 px-2 text-yellow-600 text-xs">USD eq.</div>
              </div>
              <!-- Sections -->
              <template x-for="section in reportTree" :key="section.name">
                <div>
                  <!-- Section label -->
                  <div class="border-t border-border py-2 px-2 text-blue-400 font-semibold text-xs" x-text="section.name"></div>
                  <!-- Groups and their leaves interleaved -->
                  <template x-for="group in section.groups" :key="group.name">
                    <div>
                      <!-- Group header -->
                      <div class="report-row cursor-pointer hover:bg-gray-800/40" @click="toggleReportGroup(section.name + ':' + group.name)">
                        <div class="py-1.5 px-2 pl-4 text-gray-300 text-xs">
                          <span class="text-[10px] inline-block w-3 transition-transform" :class="openReportGroups[section.name + ':' + group.name] ? 'rotate-90' : ''">&#9654;</span>
                          <span x-text="group.name"></span>
                        </div>
                        <template x-for="cur in reportCurrencies" :key="cur">
                          <div class="text-right py-1.5 px-2 text-xs"
                              :class="(group.totals[cur] || 0) < 0 ? 'text-red-400' : 'text-gray-300'"
                              x-text="fmtAmount(group.totals[cur], cur)"></div>
                        </template>
                        <div class="text-right py-1.5 px-2 text-xs"
                            :class="sumUsd(group.totals) < 0 ? 'text-red-400' : 'text-yellow-600/70'"
                            x-text="fmtAmount(sumUsd(group.totals), 'USD')"></div>
                      </div>
                      <!-- Leaves -->
                      <template x-if="openReportGroups[section.name + ':' + group.name]">
                        <div>
                          <template x-for="leaf in group.leaves" :key="leaf.name">
                            <div class="report-row hover:bg-gray-800/20">
                              <div class="py-1 px-2 pl-10 text-xs">
                                <span class="text-gray-500 hover:text-purple-400 cursor-pointer hover:underline" x-text="leaf.name" @click.stop="openAccount(acctFullName(section.name, group.name, leaf.name))"></span>
                              </div>
                              <template x-for="cur in reportCurrencies" :key="cur">
                                <div class="text-right py-1 px-2 text-xs"
                                    :class="(leaf.amounts[cur] || 0) < 0 ? 'text-red-400' : 'text-gray-400'"
                                    x-text="fmtAmount(leaf.amounts[cur], cur)"></div>
                              </template>
                              <div class="text-right py-1 px-2 text-xs"
                                  :class="sumUsd(leaf.amounts) < 0 ? 'text-red-400' : 'text-yellow-600/50'"
                                  x-text="fmtAmount(sumUsd(leaf.amounts), 'USD')"></div>
                            </div>
                          </template>
                        </div>
                      </template>
                    </div>
                  </template>
                  <!-- Section total -->
                  <div class="report-row border-t border-border">
                    <div class="py-2 px-2 text-xs font-semibold" x-text="'Total ' + section.name"></div>
                    <template x-for="cur in reportCurrencies" :key="cur">
                      <div class="text-right py-2 px-2 text-xs font-semibold"
                          :class="(section.totals[cur] || 0) < 0 ? 'text-red-400' : 'text-gray-200'"
                          x-text="fmtAmount(section.totals[cur], cur)"></div>
                    </template>
                    <div class="text-right py-2 px-2 text-xs font-semibold"
                        :class="sumUsd(section.totals) < 0 ? 'text-red-400' : 'text-yellow-600'"
                        x-text="fmtAmount(sumUsd(section.totals), 'USD')"></div>
                  </div>
                </div>
              </template>
              <!-- Grand total -->
              <div class="report-row border-t-2 border-gray-600">
                <div class="py-2 px-2 text-sm font-semibold text-gray-100" x-text="page === 'pnl' ? 'Net Income' : 'Net Worth'"></div>
                <template x-for="cur in reportCurrencies" :key="cur">
                  <div class="text-right py-2 px-2 text-sm font-semibold"
                      :class="(reportGrandTotal[cur] || 0) < 0 ? 'text-red-400' : 'text-green-400'"
                      x-text="fmtAmount(reportGrandTotal[cur], cur)"></div>
                </template>
                <div class="text-right py-2 px-2 text-sm font-semibold"
                    :class="sumUsd(reportGrandTotal) < 0 ? 'text-red-400' : 'text-yellow-500'"
                    x-text="fmtAmount(sumUsd(reportGrandTotal), 'USD')"></div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </template>

  <!-- Trial Balance page -->
  <template x-if="page === 'trialbal'">
    <div class="flex flex-col flex-1 overflow-hidden">
      <div x-show="trialBalLoading" class="flex-1 flex items-center justify-center">
        <div class="spinner"></div>
      </div>
      <div x-show="!trialBalLoading" class="flex-1 overflow-auto p-4">
        <div x-show="trialBalData.length === 0" class="text-gray-500 text-center mt-8">No data.</div>
        <template x-if="trialBalData.length > 0">
          <div>
            <div class="report-grid" :style="'--cols:' + (trialBalCurrencies.length + 2)">
              <!-- Header -->
              <div class="report-row border-b border-border">
                <div class="py-2 px-2 text-gray-500 text-xs">Account</div>
                <template x-for="cur in trialBalCurrencies" :key="'tbh-'+cur">
                  <div class="text-right py-2 px-2 text-gray-500 text-xs" x-text="cur"></div>
                </template>
                <div class="text-right py-2 px-2 text-yellow-600 text-xs">USD eq.</div>
              </div>
              <!-- Sections -->
              <template x-for="section in trialBalTree" :key="section.name">
                <div>
                  <div class="border-t border-border py-2 px-2 text-blue-400 font-semibold text-xs" x-text="section.name"></div>
                  <template x-for="group in section.groups" :key="group.name">
                    <div>
                      <div class="report-row cursor-pointer hover:bg-gray-800/40" @click="toggleReportGroup('tb:' + section.name + ':' + group.name)">
                        <div class="py-1.5 px-2 pl-4 text-gray-300 text-xs">
                          <span class="text-[10px] inline-block w-3 transition-transform" :class="openReportGroups['tb:' + section.name + ':' + group.name] ? 'rotate-90' : ''">&#9654;</span>
                          <span x-text="group.name"></span>
                        </div>
                        <template x-for="cur in trialBalCurrencies" :key="cur">
                          <div class="text-right py-1.5 px-2 text-xs"
                              :class="(group.totals[cur] || 0) < 0 ? 'text-red-400' : 'text-gray-300'"
                              x-text="fmtAmount(group.totals[cur], cur)"></div>
                        </template>
                        <div class="text-right py-1.5 px-2 text-xs"
                            :class="sumUsdLatest(group.totals) < 0 ? 'text-red-400' : 'text-yellow-600/70'"
                            x-text="fmtAmount(sumUsdLatest(group.totals), 'USD')"></div>
                      </div>
                      <template x-if="openReportGroups['tb:' + section.name + ':' + group.name]">
                        <div>
                          <template x-for="leaf in group.leaves" :key="leaf.name">
                            <div class="report-row hover:bg-gray-800/20">
                              <div class="py-1 px-2 pl-10 text-xs">
                                <span class="text-gray-500 hover:text-purple-400 cursor-pointer hover:underline" x-text="leaf.name" @click.stop="openAccount(acctFullName(section.name, group.name, leaf.name))"></span>
                              </div>
                              <template x-for="cur in trialBalCurrencies" :key="cur">
                                <div class="text-right py-1 px-2 text-xs"
                                    :class="(leaf.amounts[cur] || 0) < 0 ? 'text-red-400' : 'text-gray-400'"
                                    x-text="fmtAmount(leaf.amounts[cur], cur)"></div>
                              </template>
                              <div class="text-right py-1 px-2 text-xs"
                                  :class="sumUsdLatest(leaf.amounts) < 0 ? 'text-red-400' : 'text-yellow-600/50'"
                                  x-text="fmtAmount(sumUsdLatest(leaf.amounts), 'USD')"></div>
                            </div>
                          </template>
                        </div>
                      </template>
                    </div>
                  </template>
                  <!-- Section total -->
                  <div class="report-row border-t border-border">
                    <div class="py-2 px-2 text-xs font-semibold" x-text="'Total ' + section.name"></div>
                    <template x-for="cur in trialBalCurrencies" :key="cur">
                      <div class="text-right py-2 px-2 text-xs font-semibold"
                          :class="(section.totals[cur] || 0) < 0 ? 'text-red-400' : 'text-gray-200'"
                          x-text="fmtAmount(section.totals[cur], cur)"></div>
                    </template>
                    <div class="text-right py-2 px-2 text-xs font-semibold"
                        :class="sumUsdLatest(section.totals) < 0 ? 'text-red-400' : 'text-yellow-600'"
                        x-text="fmtAmount(sumUsdLatest(section.totals), 'USD')"></div>
                  </div>
                </div>
              </template>
              <!-- Grand total (should be zero) -->
              <div class="report-row border-t-2 border-gray-600">
                <div class="py-2 px-2 text-sm font-semibold text-gray-100">Total (should be zero)</div>
                <template x-for="cur in trialBalCurrencies" :key="cur">
                  <div class="text-right py-2 px-2 text-sm font-semibold"
                      :class="Math.abs(trialBalGrandTotal[cur] || 0) < 0.01 ? 'text-green-400' : 'text-red-400'"
                      x-text="fmtAmount(trialBalGrandTotal[cur], cur)"></div>
                </template>
                <div class="text-right py-2 px-2 text-sm font-semibold"
                    :class="Math.abs(sumUsdLatest(trialBalGrandTotal)) < 1 ? 'text-green-400' : 'text-red-400'"
                    x-text="fmtAmount(sumUsdLatest(trialBalGrandTotal), 'USD')"></div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </template>

  <!-- Ledger page -->
  <div x-show="page === 'ledger'" class="flex flex-1 overflow-hidden">

    <!-- Sidebar -->
    <div class="w-80 min-w-[280px] border-r border-border overflow-y-auto bg-surface shrink-0">
      <template x-for="year in years" :key="year.name">
        <div>
          <button
            @click="toggleYear(year.name)" @keydown.prevent
            tabindex="-1"
            class="flex items-center justify-between w-full px-3 py-2 text-xs font-semibold text-gray-400 bg-gray-900/50 border-b border-border hover:bg-gray-800/50 sticky top-0 z-10"
          >
            <div class="flex items-center gap-2">
              <span class="text-[10px] transition-transform" :class="openYears[year.name] ? 'rotate-90' : ''">&#9654;</span>
              <span x-text="year.name"></span>
            </div>
            <span class="text-gray-600" x-text="year.events.length"></span>
          </button>
          <div x-show="openYears[year.name]" x-collapse>
            <template x-for="event in year.events" :key="event.folder">
              <button
                @click="selectByFolder(event.folder)" @keydown.prevent
                :data-idx="event._idx"
                tabindex="-1"
                :class="event._idx === selectedIdx ? 'bg-gray-800/60 border-l-2 border-blue-400 pl-3' : 'border-l-2 border-transparent pl-3.5'"
                class="sidebar-item block w-full text-left px-3 py-2 border-b border-border hover:bg-gray-800/40"
              >
                <div class="flex items-center gap-2">
                  <span class="text-[11px] text-gray-500" x-text="event.date"></span>
                  <span
                    :class="event.reconciled ? 'text-green-600' : 'text-yellow-600'"
                    class="text-[10px]"
                    x-text="event.reconciled ? '\u2713' : '\u25cb'"
                  ></span>
                </div>
                <div class="text-sm mt-0.5 leading-snug" x-text="event.title"></div>
                <div class="flex flex-wrap gap-1 mt-1">
                  <template x-for="link in event.links" :key="link">
                    <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-950 text-blue-400 border border-blue-900" x-text="link"></span>
                  </template>
                </div>
              </button>
            </template>
          </div>
        </div>
      </template>
    </div>

    <!-- Detail panel -->
    <div class="flex-1 flex flex-col overflow-hidden" x-show="selected">

      <!-- Detail header -->
      <div class="px-4 py-2.5 bg-surface border-b border-border shrink-0">
        <h2 class="text-sm font-semibold" x-text="selected?.title"></h2>
        <div class="text-[11px] text-gray-500 mt-0.5" x-text="selected?.relFolder"></div>
      </div>

      <!-- View tabs -->
      <div class="flex bg-surface border-b border-border shrink-0">
        <button
          @click="view = 'split'"
          :class="view === 'split' ? 'text-blue-400 border-blue-400' : 'text-gray-500 border-transparent'"
          class="px-4 py-2 text-xs border-b-2 hover:text-gray-300 transition-colors"
        >Split</button>
        <button
          @click="view = 'entries'"
          :class="view === 'entries' ? 'text-blue-400 border-blue-400' : 'text-gray-500 border-transparent'"
          class="px-4 py-2 text-xs border-b-2 hover:text-gray-300 transition-colors"
        >Entries</button>
        <template x-for="(pdf, j) in (selected?.pdfs || [])" :key="pdf">
          <button
            @click="view = 'pdf-' + j"
            :class="view === 'pdf-' + j ? 'text-blue-400 border-blue-400' : 'text-gray-500 border-transparent'"
            class="px-4 py-2 text-xs border-b-2 hover:text-gray-300 transition-colors"
            x-text="pdf.split('/').pop()"
          ></button>
        </template>
      </div>

      <!-- Loading entry spinner -->
      <div x-show="loadingEntry" class="flex-1 flex items-center justify-center">
        <div class="spinner"></div>
      </div>

      <!-- Content area (hidden while loading entry) -->
      <div x-show="!loadingEntry" class="flex-1 flex flex-col overflow-hidden">

        <!-- Split view -->
        <div x-show="view === 'split'" class="flex-1 flex overflow-hidden">
          <div class="flex-1 overflow-auto p-4 border-r border-border">
            <pre class="text-sm leading-relaxed whitespace-pre-wrap" x-html="highlightBeancount(currentEntries)"></pre>
          </div>
          <div class="flex-1">
            <template x-if="selected?.pdfs?.length">
              <iframe :src="'?pdf=' + encodeURIComponent(selected.pdfs[0])" class="w-full h-full border-0"></iframe>
            </template>
            <template x-if="!selected?.pdfs?.length">
              <div class="flex items-center justify-center h-full text-gray-600">No source PDF filed.</div>
            </template>
          </div>
        </div>

        <!-- Entries only -->
        <div x-show="view === 'entries'" class="flex-1 overflow-auto p-4">
          <pre class="text-sm leading-relaxed whitespace-pre-wrap" x-html="highlightBeancount(currentEntries)"></pre>
        </div>

        <!-- PDF only tabs -->
        <template x-for="(pdf, j) in (selected?.pdfs || [])" :key="'pdfview-' + pdf">
          <div x-show="view === 'pdf-' + j" class="flex-1">
            <iframe :src="'?pdf=' + encodeURIComponent(pdf)" class="w-full h-full border-0"></iframe>
          </div>
        </template>

      </div>
    </div>
  </div>

    </div>
  </template>
</div>

<script>
function ledgerApp() {
  return {
    page: 'ledger',
    events: [],
    openYears: {},
    selectedIdx: 0,
    search: '',
    view: 'split',
    loading: true,
    error: null,
    loadingEntry: false,
    currentEntries: '',
    entryCache: {},
    // Report state
    reportYears: [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026],
    reportYear: 2024,
    reportData: [],
    reportLoading: false,
    reportCache: {},
    openReportGroups: {},
    fxRates: {},  // {year: {currency: rate_to_usd}}
    // Account detail modal
    acctModal: false,
    acctName: '',
    acctJournal: [],
    acctLoading: false,
    acctCache: {},
    // Trial balance state
    trialBalData: [],
    trialBalLoading: false,
    trialBalLoaded: false,

    get selected() {
      return this.events[this.selectedIdx] || null;
    },
    get filteredEvents() {
      if (!this.search) return this.events;
      const q = this.search.toLowerCase();
      return this.events.filter(e =>
        e.title.toLowerCase().includes(q) ||
        e.folder.toLowerCase().includes(q) ||
        e.date.includes(q) ||
        e.links.some(l => l.toLowerCase().includes(q)) ||
        e.tags.some(t => t.toLowerCase().includes(q))
      );
    },
    get years() {
      const yearMap = {};
      this.filteredEvents.forEach(e => {
        if (!yearMap[e.year]) yearMap[e.year] = [];
        yearMap[e.year].push(e);
      });
      // When searching, auto-open all matching years
      if (this.search) {
        Object.keys(yearMap).forEach(y => this.openYears[y] = true);
      }
      return Object.keys(yearMap).sort().reverse().map(y => ({ name: y, events: yearMap[y] }));
    },
    async init() {
      window._openAccount = (name) => this.openAccount(name);
      try {
        const [evRes, fxRes] = await Promise.all([
          fetch('?api=events'),
          fetch('?api=fxrates'),
        ]);
        if (!evRes.ok) throw new Error('Failed to load events (HTTP ' + evRes.status + ')');
        this.events = await evRes.json();
        if (fxRes.ok) this.fxRates = await fxRes.json();
      } catch (e) {
        this.error = e.message;
        this.loading = false;
        return;
      }

      // Attach flat index to each event
      this.events.forEach((e, i) => e._idx = i);

      // Open the most recent year by default
      const yearNames = [...new Set(this.events.map(e => e.year))].sort().reverse();
      if (yearNames.length) this.openYears[yearNames[0]] = true;

      // Restore state from URL hash
      const hash = window.location.hash.slice(1);
      if (hash === 'pnl' || hash.startsWith('pnl/')) {
        this.page = 'pnl';
        const y = parseInt(hash.split('/')[1]);
        if (y >= 2018 && y <= 2030) this.reportYear = y;
      } else if (hash === 'balsheet' || hash.startsWith('balsheet/')) {
        this.page = 'balsheet';
        const y = parseInt(hash.split('/')[1]);
        if (y >= 2018 && y <= 2030) this.reportYear = y;
      } else if (hash === 'trialbal') {
        this.page = 'trialbal';
      } else if (hash) {
        const idx = this.events.findIndex(e => e.folder === hash);
        if (idx >= 0) {
          this.selectedIdx = idx;
          this.openYears[this.events[idx].year] = true;
        }
      }

      this.loading = false;

      // Load initial view
      if (this.page === 'trialbal') {
        this.fetchTrialBal();
      } else if (this.page === 'pnl' || this.page === 'balsheet') {
        this.fetchReport();
      } else if (this.selected) {
        await this.loadEntry(this.selected.relFolder);
      }

      this.$nextTick(() => {
        const el = document.querySelector(`.sidebar-item[data-idx="${this.selectedIdx}"]`);
        el?.scrollIntoView({ block: 'center' });
      });

      // Keep selection in sync when search filters change
      this.$watch('search', () => {
        this.$nextTick(() => {
          const filtered = this.filteredEvents;
          if (filtered.length && !filtered.some(e => e._idx === this.selectedIdx)) {
            this.select(filtered[0]._idx);
          }
        });
      });

      // Keyboard navigation
      document.addEventListener('keydown', (e) => {
        const inInput = e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA';
        if (inInput) {
          if (e.key === 'Escape') {
            this.search = '';
            e.target.blur();
          }
          return;
        }
        if (e.key === '/' || (e.key === 'f' && (e.metaKey || e.ctrlKey))) {
          e.preventDefault();
          this.$refs.searchInput.focus();
        } else if (e.key === 'j' || e.key === 'ArrowDown') {
          e.preventDefault();
          this.selectNext(1);
        } else if (e.key === 'k' || e.key === 'ArrowUp') {
          e.preventDefault();
          this.selectNext(-1);
        } else if (e.key === '1') { this.view = 'split'; }
        else if (e.key === '2') { this.view = 'entries'; }
        else if (e.key === '3' && this.selected?.pdfs?.length) { this.view = 'pdf-0'; }
      });
    },

    async loadEntry(relFolder) {
      if (this.entryCache[relFolder]) {
        this.currentEntries = this.entryCache[relFolder];
        return;
      }
      this.loadingEntry = true;
      this.currentEntries = '';
      try {
        const res = await fetch('?api=entry&folder=' + encodeURIComponent(relFolder));
        if (!res.ok) throw new Error('Failed to load');
        const data = await res.json();
        this.entryCache[relFolder] = data.entries;
        this.currentEntries = data.entries;
      } catch {
        this.currentEntries = '; Error loading entries';
      }
      this.loadingEntry = false;
    },

    toggleYear(name) {
      this.openYears[name] = !this.openYears[name];
    },
    visibleIndices() {
      return this.filteredEvents.filter(e => this.openYears[e.year]).map(e => e._idx);
    },
    selectNext(delta) {
      const visible = this.visibleIndices();
      if (!visible.length) return;
      const curPos = visible.indexOf(this.selectedIdx);
      let nextPos;
      if (curPos === -1) {
        nextPos = delta > 0 ? 0 : visible.length - 1;
      } else {
        nextPos = Math.max(0, Math.min(visible.length - 1, curPos + delta));
      }
      this.select(visible[nextPos]);
    },
    selectByFolder(folder) {
      const idx = this.events.findIndex(e => e.folder === folder);
      if (idx >= 0) this.select(idx);
    },
    select(i) {
      this.selectedIdx = i;
      this.view = 'split';
      const event = this.events[i];
      if (event) {
        this.openYears[event.year] = true;
        history.replaceState(null, '', '#' + event.folder);
        this.loadEntry(event.relFolder);
      }
      this.$nextTick(() => {
        const el = document.querySelector(`.sidebar-item[data-idx="${i}"]`);
        el?.scrollIntoView({ block: 'nearest' });
      });
    },
    // Report methods
    switchPage(p) {
      this.page = p;
      if (p === 'trialbal') {
        history.replaceState(null, '', '#trialbal');
        this.fetchTrialBal();
      } else if (p === 'pnl' || p === 'balsheet') {
        history.replaceState(null, '', '#' + p + '/' + this.reportYear);
        this.fetchReport();
      } else {
        const event = this.events[this.selectedIdx];
        history.replaceState(null, '', event ? '#' + event.folder : '#');
      }
    },
    selectReportYear(y) {
      this.reportYear = y;
      history.replaceState(null, '', '#' + this.page + '/' + y);
      this.fetchReport();
    },
    async fetchReport() {
      const key = this.page + '-' + this.reportYear;
      if (this.reportCache[key]) {
        this.reportData = this.reportCache[key];
        return;
      }
      this.reportLoading = true;
      this.reportData = [];
      try {
        const res = await fetch('?api=' + this.page + '&year=' + this.reportYear);
        if (!res.ok) throw new Error('Failed');
        const data = await res.json();
        this.reportCache[key] = data;
        this.reportData = data;
      } catch {
        this.reportData = [];
      }
      this.reportLoading = false;
    },
    toggleReportGroup(key) {
      this.openReportGroups[key] = !this.openReportGroups[key];
    },
    // Account detail
    acctFullName(section, group, leaf) {
      if (leaf === group) return section + ':' + group;
      return section + ':' + group + ':' + leaf;
    },
    async openAccount(name) {
      this.acctName = name;
      this.acctModal = true;
      if (this.acctCache[name]) {
        this.acctJournal = this.acctCache[name];
        return;
      }
      this.acctLoading = true;
      this.acctJournal = [];
      try {
        const res = await fetch('?api=account&name=' + encodeURIComponent(name));
        if (!res.ok) throw new Error('Failed');
        const data = await res.json();
        this.acctCache[name] = data;
        this.acctJournal = data;
      } catch { this.acctJournal = []; }
      this.acctLoading = false;
    },
    // Trial Balance
    async fetchTrialBal() {
      if (this.trialBalLoaded) return;
      this.trialBalLoading = true;
      try {
        const res = await fetch('?api=trialbal');
        if (!res.ok) throw new Error('Failed');
        this.trialBalData = await res.json();
        this.trialBalLoaded = true;
      } catch { this.trialBalData = []; }
      this.trialBalLoading = false;
    },
    get trialBalCurrencies() {
      const curs = new Set();
      this.trialBalData.forEach(r => curs.add(r.currency));
      const order = ['USD', 'EUR', 'GBP', 'ILS', 'JPY', 'PLN', 'CHF', 'AUD'];
      return order.filter(c => curs.has(c));
    },
    get trialBalTree() {
      const sectionOrder = ['Assets', 'Liabilities', 'Equity', 'Income', 'Expenses'];
      const sections = {};
      sectionOrder.forEach(s => sections[s] = { name: s, groups: {}, totals: {} });

      this.trialBalData.forEach(row => {
        const parts = row.account.split(':');
        const sectionName = parts[0];
        if (!sections[sectionName]) return;
        const section = sections[sectionName];
        const groupName = parts[1] || 'Other';
        const leafName = parts.slice(2).join(':') || groupName;

        if (!section.groups[groupName]) {
          section.groups[groupName] = { name: groupName, leaves: {}, totals: {} };
        }
        const group = section.groups[groupName];
        if (!group.leaves[leafName]) {
          group.leaves[leafName] = { name: leafName, amounts: {} };
        }
        group.leaves[leafName].amounts[row.currency] = (group.leaves[leafName].amounts[row.currency] || 0) + row.amount;
        group.totals[row.currency] = (group.totals[row.currency] || 0) + row.amount;
        section.totals[row.currency] = (section.totals[row.currency] || 0) + row.amount;
      });

      return sectionOrder.map(s => {
        const section = sections[s];
        const groupList = Object.values(section.groups).map(g => ({
          ...g,
          leaves: Object.values(g.leaves).sort((a, b) => a.name.localeCompare(b.name))
        })).sort((a, b) => a.name.localeCompare(b.name));
        return { ...section, groups: groupList };
      }).filter(s => s.groups.length > 0);
    },
    get trialBalGrandTotal() {
      const totals = {};
      this.trialBalTree.forEach(section => {
        this.trialBalCurrencies.forEach(cur => {
          totals[cur] = (totals[cur] || 0) + (section.totals[cur] || 0);
        });
      });
      return totals;
    },
    sumUsdLatest(amounts) {
      // Use most recent year's FX rates
      const rates = this.fxRates[2026] || this.fxRates[2025] || {};
      let total = 0;
      for (const [cur, val] of Object.entries(amounts || {})) {
        if (Math.abs(val) < 0.005) continue;
        const rate = cur === 'USD' ? 1 : (rates[cur] || 0);
        total += val * rate;
      }
      return total;
    },
    get reportCurrencies() {
      const curs = new Set();
      this.reportData.forEach(r => curs.add(r.currency));
      const order = ['USD', 'EUR', 'GBP', 'ILS', 'JPY', 'PLN', 'CHF', 'AUD'];
      return order.filter(c => curs.has(c));
    },
    get reportTree() {
      const isPnl = this.page === 'pnl';
      const sectionOrder = isPnl ? ['Income', 'Expenses'] : ['Assets', 'Liabilities', 'Equity'];
      const sections = {};
      sectionOrder.forEach(s => sections[s] = { name: s, groups: {}, totals: {} });

      this.reportData.forEach(row => {
        const parts = row.account.split(':');
        const sectionName = parts[0];
        if (!sections[sectionName]) return;
        const section = sections[sectionName];
        const groupName = parts[1] || 'Other';
        const leafName = parts.slice(2).join(':') || groupName;

        if (!section.groups[groupName]) {
          section.groups[groupName] = { name: groupName, leaves: {}, totals: {} };
        }
        const group = section.groups[groupName];

        // Apply sign convention: flip Income signs for P&L, flip Liabilities/Equity for BS
        let amount = row.amount;
        if (isPnl && sectionName === 'Income') amount = -amount;
        if (!isPnl && (sectionName === 'Liabilities' || sectionName === 'Equity')) amount = -amount;

        if (!group.leaves[leafName]) {
          group.leaves[leafName] = { name: leafName, amounts: {} };
        }
        group.leaves[leafName].amounts[row.currency] = (group.leaves[leafName].amounts[row.currency] || 0) + amount;
        group.totals[row.currency] = (group.totals[row.currency] || 0) + amount;
        section.totals[row.currency] = (section.totals[row.currency] || 0) + amount;
      });

      return sectionOrder.map(s => {
        const section = sections[s];
        const groupList = Object.values(section.groups).map(g => ({
          ...g,
          leaves: Object.values(g.leaves).sort((a, b) => a.name.localeCompare(b.name))
        })).sort((a, b) => a.name.localeCompare(b.name));
        return { ...section, groups: groupList };
      }).filter(s => s.groups.length > 0);
    },
    get reportGrandTotal() {
      const totals = {};
      const isPnl = this.page === 'pnl';
      this.reportTree.forEach(section => {
        this.reportCurrencies.forEach(cur => {
          const val = section.totals[cur] || 0;
          if (isPnl) {
            // Net = Income - Expenses (Income already positive, Expenses already positive)
            const sign = section.name === 'Expenses' ? -1 : 1;
            totals[cur] = (totals[cur] || 0) + val * sign;
          } else {
            // Net Worth = Assets - Liabilities - Equity
            const sign = (section.name === 'Liabilities' || section.name === 'Equity') ? -1 : 1;
            totals[cur] = (totals[cur] || 0) + val * sign;
          }
        });
      });
      return totals;
    },
    sumUsd(amounts) {
      const rates = this.fxRates[this.reportYear] || {};
      let total = 0;
      for (const [cur, val] of Object.entries(amounts || {})) {
        if (Math.abs(val) < 0.005) continue;
        const rate = cur === 'USD' ? 1 : (rates[cur] || 0);
        total += val * rate;
      }
      return total;
    },
    fmtAmount(val, currency) {
      if (val === undefined || val === null || Math.abs(val) < 0.005) return '';
      const decimals = currency === 'JPY' ? 0 : 2;
      return new Intl.NumberFormat('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(val);
    },

    highlightBeancount(text) {
      if (!text) return '';
      // esc() runs first to neutralize all HTML - regexes only add safe <span> tags
      return text.split('\n').map(line => {
        if (/^\s*;/.test(line)) {
          return `<span class="text-gray-500">${this.esc(line)}</span>`;
        }
        let s = this.esc(line);
        s = s.replace(/(\d{4}-\d{2}-\d{2})/g, '<span class="text-green-400">$1</span>');
        s = s.replace(/&quot;([^&]*?)&quot;/g, '<span class="text-sky-300">&quot;$1&quot;</span>');
        s = s.replace(/(\^[\w-]+)/g, '<span class="text-blue-400">$1</span>');
        s = s.replace(/(#[\w-]+)/g, '<span class="text-green-400">$1</span>');
        s = s.replace(/((?:Assets|Liabilities|Income|Expenses|Equity)(?::[\w-]+)+)/g, '<span class="text-purple-400 hover:underline cursor-pointer" onclick="window._openAccount(\'$1\')">$1</span>');
        s = s.replace(/([\d,]+\.\d{2})\s+(USD|GBP|EUR|ILS|PLN|CHF|JPY|AUD)/g, '<span class="text-orange-400">$1 $2</span>');
        s = s.replace(/^(\s+)([\w-]+)(:)/gm, '$1<span class="text-gray-500">$2$3</span>');
        return s;
      }).join('\n');
    },
    esc(s) {
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }
  };
}
</script>

</body>
</html>
