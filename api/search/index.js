const axios = require('axios');
const cheerio = require('cheerio');

function isLowValue(text) {
  if (!text || text.length < 20) return true;
  const badPhrases = [
    'Loading...',
    'Creating an answer',
    'AI-generated answer',
    'We weren\'t able to create a summary',
    'Refresh your page'
  ];
  return badPhrases.some(phrase => text.includes(phrase));
}

function isQualityContent(text) {
  if (!text) return false;
  const words = text.trim().split(/\s+/).filter(Boolean);
  let wordCount = words.length;
  
  const cjkPattern = /[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf\uac00-\ud7af]/;
  if (cjkPattern.test(text)) {
    wordCount = text.replace(/\s+/g, '').length;
  }
  
  if (wordCount < 30) return false;
  
  const cleanLetters = text.replace(/[^\p{L}]/gu, '');
  if (cleanLetters.length === 0) return true;
  const upperCaseCount = (cleanLetters.match(/\p{Lu}/gu) || []).length;
  const upperCaseRatio = upperCaseCount / cleanLetters.length;
  
  return upperCaseRatio < 0.45;
}

function cleanHtmlText(html) {
  if (!html) return '';
  const $ = cheerio.load(html);
  $('script, style, nav, footer, header, .advertisement, .ads, noscript, iframe').remove();
  $('*').each((i, elem) => {
    $(elem).removeAttr('class').removeAttr('id').removeAttr('style');
  });
  return $('body').text().trim().replace(/\s+/g, ' ');
}

async function fetchWithFallback(url, maxSymbols = 5000, maxRetries = 2) {
  for (let i = 0; i < maxRetries; i++) {
    try {
      const response = await axios.get(url, {
        timeout: 8000,
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.9,*;q=0.5',
          'Accept-Encoding': 'gzip, deflate, br',
          'Connection': 'keep-alive',
          'Upgrade-Insecure-Requests': '1'
        }
      });
      const cleaned = cleanHtmlText(response.data);
      if (isQualityContent(cleaned)) {
        return cleaned.substring(0, maxSymbols);
      }
    } catch (error) {
      if (i === maxRetries - 1) throw error;
    }
  }
  throw new Error('Content not qualified');
}

function extractRealUrl(href) {
  if (!href) return null;
  if (!href.includes('r.search.yahoo.com')) {
    try {
      new URL(href);
      return href;
    } catch {
      return null;
    }
  }
  const match = href.match(/RU=([^/]+)/);
  if (match && match[1]) {
    try {
      const decoded = decodeURIComponent(match[1]);
      new URL(decoded);
      return decoded.split('#')[0].split('?')[0];
    } catch (e) {
      return null;
    }
  }
  return null;
}

async function searchEngineQuery(query) {
  const formattedQuery = encodeURIComponent(query.trim());
  let html = null;
  let source = 'yahoo';

  try {
    const yahooUrl = `https://search.yahoo.com/search?p=${formattedQuery}`;
    const resp = await axios.get(yahooUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,*;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none'
      },
      timeout: 10000
    });
    html = resp.data;
  } catch (error) {
    console.log(`Yahoo search failed for query "${query}": ${error.message}`);
  }

  if (!html) {
    try {
      source = 'duckduckgo';
      const ddgUrl = `https://html.duckduckgo.com/html/?q=${formattedQuery}`;
      const resp = await axios.get(ddgUrl, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.9,*;q=0.5',
          'Accept-Encoding': 'gzip, deflate, br'
        },
        timeout: 10000
      });
      html = resp.data;
    } catch (error) {
      console.log(`DuckDuckGo fallback search failed for query "${query}": ${error.message}`);
      return [];
    }
  }

  const results = [];
  const $ = cheerio.load(html);

  if (source === 'yahoo') {
    $('.algo, .algo-sr, .dd.algo, div[class*="algo"]').each((i, elem) => {
      const $elem = $(elem);
      const linkEl = $elem.find('a[href*="/RU="], h3 a, a.ac-algo').first();
      const href = linkEl.attr('href');
      const snippet = $elem.find('.compText, div[class*="compText"]').text().trim();
      const realUrl = extractRealUrl(href);
      if (realUrl && snippet) {
        results.push({ realUrl, snippet });
      }
    });

    if (results.length === 0) {
      $('a[href*="/RU="]').each((i, elem) => {
        const href = $(elem).attr('href');
        const realUrl = extractRealUrl(href);
        const parent = $(elem).closest('div');
        const snippet = parent.parent().find('.compText, div[class*="compText"]').first().text().trim() ||
                        parent.nextAll('.compText, div[class*="compText"]').first().text().trim();
        if (realUrl && snippet && snippet.length > 10) {
          results.push({ realUrl, snippet });
        }
      });
    }
  } else if (source === 'duckduckgo') {
    $('.result').each((i, elem) => {
      const $elem = $(elem);
      const linkEl = $elem.find('.result__title a').first();
      const href = linkEl.attr('href');
      const snippet = $elem.find('.result__snippet').text().trim();
      
      let realUrl = null;
      if (href) {
        try {
          if (href.startsWith('/l/?')) {
            const searchParams = new URLSearchParams(href.split('?')[1]);
            const uddg = searchParams.get('uddg');
            if (uddg) {
              realUrl = decodeURIComponent(uddg);
            }
          } else {
            realUrl = href;
          }
          if (realUrl) {
            new URL(realUrl);
            realUrl = realUrl.split('#')[0].split('?')[0];
          }
        } catch {
          realUrl = null;
        }
      }

      if (realUrl && snippet) {
        results.push({ realUrl, snippet });
      }
    });
  }

  return results;
}

async function handler(request, response) {
  response.setHeader('Access-Control-Allow-Origin', '*');
  response.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  response.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (request.method === 'OPTIONS') {
    return response.status(200).end();
  }

  let search = [];
  let maxSmallSnippets = 5;
  let maxLargeSnippets = 2;
  let maxLargeSnippetSymbols = 4500;

  try {
    if (request.method === 'GET') {
      const url = request.url;
      const queryStart = url.indexOf('?');
      if (queryStart !== -1) {
        const queryString = url.substring(queryStart + 1);
        const params = new URLSearchParams(queryString);
        const searchParam = params.get('search');
        search = searchParam ? searchParam.split('~').map(s => s.trim()).filter(Boolean) : [];
        const maxSmall = parseInt(params.get('maxSmallSnippets'), 10);
        const maxLarge = parseInt(params.get('maxLargeSnippets'), 10);
        const maxSymbols = parseInt(params.get('maxLargeSnippetSymbols'), 10);
        if (!isNaN(maxSmall) && maxSmall >= 0) maxSmallSnippets = maxSmall;
        if (!isNaN(maxLarge) && maxLarge >= 0) maxLargeSnippets = maxLarge;
        if (!isNaN(maxSymbols) && maxSymbols > 0) maxLargeSnippetSymbols = maxSymbols;
      }
    } else if (request.method === 'POST') {
      let body = '';
      for await (const chunk of request) {
        body += chunk.toString();
      }
      let parsed;
      try {
        parsed = JSON.parse(body);
      } catch (error) {
        console.log(`JSON parse failed: ${error.message}`);
        return response.status(400).json({ success: false, error: 'Invalid JSON' });
      }
      search = Array.isArray(parsed.search) ? parsed.search : [];
      if (typeof parsed.maxSmallSnippets === 'number' && parsed.maxSmallSnippets >= 0) maxSmallSnippets = parsed.maxSmallSnippets;
      if (typeof parsed.maxLargeSnippets === 'number' && parsed.maxLargeSnippets >= 0) maxLargeSnippets = parsed.maxLargeSnippets;
      if (typeof parsed.maxLargeSnippetSymbols === 'number' && parsed.maxLargeSnippetSymbols > 0) maxLargeSnippetSymbols = parsed.maxLargeSnippetSymbols;
    } else {
      console.log(`Method not allowed: ${request.method}`);
      return response.status(405).json({ success: false, error: 'Method not allowed' });
    }

    if (!Array.isArray(search) || search.length === 0) {
      console.log('Missing or invalid search parameter');
      return response.status(400).json({ success: false, error: 'Invalid or missing "search" parameter' });
    }

    const results = [];
    const processedDomains = new Set();

    for (const query of search.slice(0, 5)) {
      const queryDomains = new Set();
      let queryShortCount = 0;
      let queryLongCount = 0;

      try {
        const queryResults = await searchEngineQuery(query);

        for (const item of queryResults) {
          if (queryShortCount >= maxSmallSnippets && queryLongCount >= maxLargeSnippets) {
            break;
          }

          const { realUrl, snippet } = item;
          if (!realUrl || !snippet) continue;

          if (maxSmallSnippets > 0 && queryShortCount < maxSmallSnippets) {
            if (snippet && !isLowValue(snippet) && snippet.length > 30) {
              results.push({
                url: realUrl,
                snippet,
                type: 'short',
                symbols: snippet.length,
                query: query
              });
              queryShortCount++;
            }
          }

          if (maxLargeSnippets > 0 && queryLongCount < maxLargeSnippets) {
            let urlObj;
            try {
              urlObj = new URL(realUrl);
            } catch (error) {
              continue;
            }
            const domain = `${urlObj.protocol}//${urlObj.hostname}`;
            if (!processedDomains.has(domain) && !queryDomains.has(domain)) {
              queryDomains.add(domain);
              processedDomains.add(domain);
              try {
                const fullContent = await fetchWithFallback(realUrl, maxLargeSnippetSymbols);
                results.push({
                  url: realUrl,
                  snippet: fullContent,
                  type: 'long',
                  symbols: fullContent.length,
                  query: query
                });
                queryLongCount++;
              } catch (error) {
                console.log(`Large snippet fetch failed for ${realUrl}: ${error.message}`);
              }
            }
          }
        }
      } catch (error) {
        console.log(`Search query execution failed for "${query}": ${error.message}`);
        continue;
      }
    }

    if (results.length === 0) {
      console.log(`No results found for queries: ${search.join(', ')}`);
      return response.status(404).json({
        success: false,
        error: 'No valid results found for the given queries'
      });
    }

    return response.status(200).json({
      results,
      success: true
    });

  } catch (error) {
    console.log(`Error: ${error.name}`);
    console.log(`Message: ${error.message}`);
    console.log(`Stack: ${error.stack?.split('\n')[1]?.trim()}`);
    return response.status(500).json({
      success: false,
      error: 'Internal server error'
    });
  }
}

module.exports = handler;