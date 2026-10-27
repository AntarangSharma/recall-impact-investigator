document.querySelectorAll('form').forEach((form) => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button');
    if (button) {
      button.disabled = true;
      button.textContent = button.dataset.loading || 'Working...';
      button.setAttribute('aria-busy', 'true');
    }
  });
});
