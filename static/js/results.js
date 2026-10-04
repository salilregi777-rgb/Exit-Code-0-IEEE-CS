(() => {
  const page = App.page;
  const {setInterval, setTimeout} = page;
 const shell=document.querySelector('[data-results-published]');
 const wasPublished=shell?.dataset.resultsPublished==='true';
 const original=App.eventState?.event_status;
 page.listen(document, 'eventstate',e=>{if((e.detail.results_published&&!wasPublished)||(original&&original!=='COMPLETED'&&e.detail.event_status==='COMPLETED'))(App.navigation ? App.navigation.refresh() : location.reload());});
})();
