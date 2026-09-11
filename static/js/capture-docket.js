(function () {
    if (location.protocol !== 'https:' || !['www.pacermonitor.com', 'pacermonitor.com'].includes(location.hostname)
        || !/^\/(public\/)?case\/\d+(\/|$)/.test(location.pathname)) {
        alert('Open the PacerMonitor case docket while signed in, then use this bookmark.');
        return;
    }
    const page = document.documentElement.cloneNode(true);
    page.querySelectorAll('script, style, link, form, input, textarea, iframe, object, embed, noscript').forEach(node => node.remove());
    page.querySelectorAll('*').forEach(node => {
        for (const attribute of [...node.attributes]) {
            if (/^on/i.test(attribute.name)) node.removeAttribute(attribute.name);
        }
    });
    const source = document.createElement('meta');
    source.name = 'docket-source-url';
    source.content = location.origin + location.pathname;
    page.querySelector('head').appendChild(source);
    const blob = new Blob(['<!doctype html>\n' + page.outerHTML], {type: 'text/html;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const download = document.createElement('a');
    download.href = url;
    download.download = 'pacermonitor-docket-' + location.pathname.match(/case\/(\d+)/)[1] + '.html';
    document.body.appendChild(download);
    download.click();
    download.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
})();
