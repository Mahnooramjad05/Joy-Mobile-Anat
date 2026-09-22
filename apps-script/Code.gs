/**
 * Device Trade-In API — Google Apps Script web app.
 *
 * Reads the "Devices" tab of the bound spreadsheet and answers JSON queries
 * from the Heyy chatbot. Deploy as: Execute as "Me", Access "Anyone".
 *
 * Endpoints (all GET, all take ?key=<API_KEY>):
 *   ?action=manufacturers
 *   ?action=models&manufacturer=Apple
 *   ?action=storage&manufacturer=Apple&model=iPhone 15 Pro
 *   ?action=conditions
 *   ?action=quote&manufacturer=Apple&model=iPhone 15 Pro&storage=128GB&condition=Intact
 *
 * Omitting `condition` from a quote returns the price for every condition,
 * which lets the bot show the full ladder in one message.
 */

var SHEET_NAME = 'Devices';
var CACHE_SECONDS = 300;

function doGet(e) {
  try {
    var params = (e && e.parameter) || {};

    var expectedKey = PropertiesService.getScriptProperties().getProperty('API_KEY');
    if (expectedKey && params.key !== expectedKey) {
      return jsonResponse({ ok: false, error: { code: 'unauthorized', message: 'Invalid or missing API key.' } });
    }

    var action = params.action || 'quote';

    switch (action) {
      case 'manufacturers': return jsonResponse({ ok: true, data: listManufacturers() });
      case 'models':        return jsonResponse({ ok: true, data: listModels(params.manufacturer) });
      case 'storage':       return jsonResponse({ ok: true, data: listStorage(params.manufacturer, params.model) });
      case 'conditions':    return jsonResponse({ ok: true, data: listConditions() });
      case 'quote':         return jsonResponse(quote(params));
      default:
        return jsonResponse({ ok: false, error: { code: 'unknown_action', message: 'Unknown action: ' + action } });
    }
  } catch (err) {
    return jsonResponse({ ok: false, error: { code: 'server_error', message: String(err) } });
  }
}

/* ---------------------------------------------------------------- queries */

function quote(params) {
  if (!params.manufacturer || !params.model || !params.storage) {
    return { ok: false, error: { code: 'missing_parameter', message: 'manufacturer, model and storage are all required.' } };
  }

  var matches = getRows().filter(function (row) {
    return row.active &&
      norm(row.manufacturer) === norm(params.manufacturer) &&
      norm(row.model_name) === norm(params.model) &&
      norm(row.storage) === norm(params.storage);
  });

  if (!matches.length) {
    return {
      ok: false,
      error: { code: 'device_not_found', message: 'No device matched that manufacturer, model and storage.' },
      suggestions: listModels(params.manufacturer)
    };
  }

  if (params.condition) {
    var exact = matches.filter(function (row) { return norm(row.condition) === norm(params.condition); });
    if (!exact.length) {
      return {
        ok: false,
        error: { code: 'condition_not_found', message: 'Unknown condition: ' + params.condition },
        suggestions: matches.map(function (row) { return row.condition; })
      };
    }
    return { ok: true, data: priceOf(exact[0]) };
  }

  return {
    ok: true,
    data: {
      device_id: matches[0].device_id,
      manufacturer: matches[0].manufacturer,
      model_name: matches[0].model_name,
      storage: matches[0].storage,
      release_year: matches[0].release_year,
      currency: 'EUR',
      prices: matches.map(function (row) {
        return { condition: row.condition, price_multiplier: row.price_multiplier, final_price: row.final_price_eur };
      })
    }
  };
}

function priceOf(row) {
  return {
    device_id: row.device_id,
    manufacturer: row.manufacturer,
    model_name: row.model_name,
    storage: row.storage,
    release_year: row.release_year,
    condition: row.condition,
    base_price: row.base_price_eur,
    price_multiplier: row.price_multiplier,
    final_price: row.final_price_eur,
    currency: 'EUR',
    last_updated: row.last_updated
  };
}

function listManufacturers() {
  return unique(activeRows().map(function (row) { return row.manufacturer; })).sort();
}

function listModels(manufacturer) {
  var rows = activeRows();
  if (manufacturer) {
    rows = rows.filter(function (row) { return norm(row.manufacturer) === norm(manufacturer); });
  }
  return unique(rows.map(function (row) { return row.model_name; })).sort();
}

function listStorage(manufacturer, model) {
  var rows = activeRows().filter(function (row) {
    return (!manufacturer || norm(row.manufacturer) === norm(manufacturer)) &&
           (!model || norm(row.model_name) === norm(model));
  });
  return unique(rows.map(function (row) { return row.storage; }));
}

function listConditions() {
  return unique(activeRows().map(function (row) { return row.condition; }));
}

/* ------------------------------------------------------------ sheet access */

/**
 * Reads the Devices tab into plain objects keyed by the header row.
 * Cached briefly so a burst of chatbot turns does not re-read the sheet.
 */
function getRows() {
  var cache = CacheService.getScriptCache();
  var cached = cache.get('devices');
  if (cached) return JSON.parse(cached);

  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) throw new Error('Sheet "' + SHEET_NAME + '" not found.');

  var values = sheet.getDataRange().getDisplayValues();
  var header = values[0].map(function (name) { return String(name).trim(); });

  var rows = values.slice(1)
    .filter(function (row) { return String(row[0]).trim() !== ''; })
    .map(function (row) {
      var obj = {};
      header.forEach(function (name, i) { obj[name] = row[i]; });
      obj.base_price_eur = Number(obj.base_price_eur);
      obj.price_multiplier = Number(obj.price_multiplier);
      obj.final_price_eur = Number(obj.final_price_eur);
      obj.release_year = Number(obj.release_year);
      obj.active = String(obj.active).toUpperCase() !== 'FALSE';
      return obj;
    });

  try {
    cache.put('devices', JSON.stringify(rows), CACHE_SECONDS);
  } catch (err) {
    // Payload over the 100KB cache limit — serve uncached rather than fail.
  }
  return rows;
}

function activeRows() {
  return getRows().filter(function (row) { return row.active; });
}

/** Clears the cache so a Phase 2 sync takes effect immediately. */
function clearCache() {
  CacheService.getScriptCache().remove('devices');
}

/* ----------------------------------------------------------------- helpers */

/** Case-, space- and punctuation-insensitive comparison key. */
function norm(value) {
  return String(value == null ? '' : value).toLowerCase().replace(/[^a-z0-9]/g, '');
}

function unique(values) {
  var seen = {};
  return values.filter(function (value) {
    if (seen[value]) return false;
    seen[value] = true;
    return true;
  });
}

function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
