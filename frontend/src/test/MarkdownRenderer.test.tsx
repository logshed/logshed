import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MarkdownRenderer, dedentLines } from '../components/common/MarkdownRenderer.tsx';

describe('MarkdownRenderer Component', () => {
  it('renders bold and inline code correctly without showing raw asterisks or backticks', () => {
    const markdown = 'The process did **not** crash and `level=info` was logged.';
    const { container } = render(<MarkdownRenderer content={markdown} />);

    // Bold should be wrapped in <strong>
    const strongEl = container.querySelector('strong');
    expect(strongEl).not.toBeNull();
    expect(strongEl?.textContent).toBe('not');

    // Inline code should be wrapped in <code>
    const codeEl = container.querySelector('code');
    expect(codeEl).not.toBeNull();
    expect(codeEl?.textContent).toBe('level=info');

    // Raw markdown tokens should NOT be rendered as literal text
    expect(screen.queryByText('**not**')).toBeNull();
  });

  it('renders numbered lists with bold step titles and fenced code blocks', () => {
    const markdown = `1. **Inspect Document 33 in Paperless-ngx:**
   Verify whether the tag \`paperless-gpt-failed\` was applied.

2. **Exclude System Tags in Configuration:**
   \`\`\`yaml
   PAPERLESS_GPT_EXCLUDE_TAGS: "paperless-gpt-failed"
   \`\`\`

3. **Restart the Paperless-GPT Container:**
   \`\`\`bash
   docker restart Paperless-GPT
   \`\`\``;

    render(<MarkdownRenderer content={markdown} />);

    // Check step numbers
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();

    // Check bold titles
    expect(screen.getByText('Inspect Document 33 in Paperless-ngx:')).toBeInTheDocument();
    expect(screen.getByText('Exclude System Tags in Configuration:')).toBeInTheDocument();
    expect(screen.getByText('Restart the Paperless-GPT Container:')).toBeInTheDocument();

    // Check code blocks
    expect(screen.getByText(/yaml/i)).toBeInTheDocument();
    expect(screen.getByText(/bash/i)).toBeInTheDocument();
    expect(screen.getByText('docker restart Paperless-GPT')).toBeInTheDocument();

    // Check that raw backticks or asterisks are not shown
    expect(screen.queryByText('**Inspect Document 33 in Paperless-ngx:**')).toBeNull();
    expect(screen.queryByText('```bash')).toBeNull();
  });

  it('correctly handles complex YAML code blocks with dashes inside numbered list items', () => {
    const markdown = `1. **Verify Document Metadata:** Log into your Paperless-ngx web interface and inspect Document #33 (\`Worm City Composting Guide\`). Remove the incorrectly generated tags (\`paperless-gpt-failed\`, \`finance\`, \`insurance\`, \`water\`).

2. **Refine Container Environment / LLM Settings:** If using local models (e.g., via Ollama), upgrade to a higher-parameter model (e.g., moving from a 3B/7B model to a 13B/70B model or a fine-tuned instruct model). Adjust container environment variables (in your Docker compose file or container template) to lower temperature and restrict tag hallucination:

   \`\`\`yaml
   environment:
     - LLM_MODEL=gpt-4o-mini # or a stronger local model like llama3:8b-instruct
     - TEMPERATURE=0.2 # Lower temperature reduces creative hallucinations
   \`\`\`

3. **Exclude Internal Tags:** Ensure Paperless-GPT is configured to ignore system processing tags during prompt evaluation so that existing error tags are not fed back into future tag suggestions.

4. **Restart Container:** Apply changes and restart the container:
   \`\`\`bash
   docker restart Paperless-GPT
   \`\`\``;

    const { container } = render(<MarkdownRenderer content={markdown} />);

    // Check all 4 items rendered
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();

    // Verify YAML code block captured the entire content including dashed lines
    const preElements = container.querySelectorAll('pre');
    expect(preElements.length).toBe(2);

    const yamlBlock = preElements[0].textContent;
    expect(yamlBlock).toContain('environment:');
    expect(yamlBlock).toContain('- LLM_MODEL=gpt-4o-mini');
    expect(yamlBlock).toContain('- TEMPERATURE=0.2');

    const bashBlock = preElements[1].textContent;
    expect(bashBlock).toContain('docker restart Paperless-GPT');
  });

  it('safely escapes HTML tags and prevents script injection', () => {
    const malicious = '<script>alert("XSS")</script><b>Injected</b> `clean_code`';
    const { container } = render(<MarkdownRenderer content={malicious} />);

    expect(container.querySelector('script')).toBeNull();
    // Raw script text is rendered as safe text
    expect(screen.getByText(/alert\("XSS"\)/)).toBeInTheDocument();
    expect(container.querySelector('code')?.textContent).toBe('clean_code');
  });

  it('sanitizes unsafe protocols in links to # to prevent XSS', () => {
    const markdown = 'Click [attack](javascript:alert(1)) or [safe link](https://example.com) or [anchor](#top).';
    render(<MarkdownRenderer content={markdown} />);

    const attackLink = screen.getByRole('link', { name: 'attack' });
    expect(attackLink).toHaveAttribute('href', '#');
    expect(attackLink).not.toHaveAttribute('target');

    const safeLink = screen.getByRole('link', { name: 'safe link' });
    expect(safeLink).toHaveAttribute('href', 'https://example.com');
    expect(safeLink).toHaveAttribute('target', '_blank');

    const anchorLink = screen.getByRole('link', { name: 'anchor' });
    expect(anchorLink).toHaveAttribute('href', '#top');
  });

  it('renders section headings followed by numbered lists and nested code blocks', () => {
    const markdown = `A critical hardware watchdog timer expired on \`homelab-host\`.

### Root Cause
The kernel log indicates a lockup.

### Remediation
1. **Inspect Historical Kernel Logs:** Examine system logs:
   \`\`\`bash
   journalctl -b -1 -p emerg..err
   \`\`\`

2. **Check Out-of-Band Hardware Logs:** Check BMC:
   \`\`\`bash
   ipmitool sel list
   \`\`\``;

    render(<MarkdownRenderer content={markdown} />);

    // Headings
    expect(screen.getByText('Root Cause')).toBeInTheDocument();
    expect(screen.getByText('Remediation')).toBeInTheDocument();

    // Step numbers 1 and 2
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();

    // Step titles
    expect(screen.getByText('Inspect Historical Kernel Logs:')).toBeInTheDocument();
    expect(screen.getByText('Check Out-of-Band Hardware Logs:')).toBeInTheDocument();

    // Code blocks
    expect(screen.getByText('journalctl -b -1 -p emerg..err')).toBeInTheDocument();
    expect(screen.getByText('ipmitool sel list')).toBeInTheDocument();
  });

  it('dedents indented fenced code blocks while preserving relative inner indentation', () => {
    const rawLines = [
      '    if [ -f /etc/config ]; then',
      '      cat /etc/config',
      '    fi',
    ];
    const dedented = dedentLines(rawLines);
    expect(dedented).toEqual([
      'if [ -f /etc/config ]; then',
      '  cat /etc/config',
      'fi',
    ]);

    const markdown = `## Instructions
    \`\`\`bash
    echo "starting"
      echo "indented step"
    echo "finished"
    \`\`\``;

    const { container } = render(<MarkdownRenderer content={markdown} />);
    const codeEl = container.querySelector('pre code');
    expect(codeEl).not.toBeNull();
    expect(codeEl?.textContent).toBe('echo "starting"\n  echo "indented step"\necho "finished"');
  });
});


