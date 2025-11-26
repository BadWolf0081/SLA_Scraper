"""
Batch Job Status Scraper
Parses TXT files containing batch job status information and converts to CSV format.
"""

import csv
import os
import re
from pathlib import Path
from typing import List, Dict, Optional


class BatchJobParser:
    """Parser for batch job status files."""
    
    def __init__(self):
        self.records = []
    
    @staticmethod
    def format_time(time_str: str) -> str:
        """Format time string from HHMM to HH:MM format."""
        if not time_str or time_str == '':
            return ''
        # Remove parentheses if present
        time_str = time_str.strip('()')
        # If it's already formatted or contains special chars, return as-is
        if ':' in time_str or '/' in time_str or len(time_str) != 4:
            return time_str
        # Format HHMM to HH:MM
        if time_str.isdigit() and len(time_str) == 4:
            return f"{time_str[:2]}:{time_str[2:]}"
        return time_str
    
    def parse_file(self, filepath: str) -> List[Dict]:
        """Parse a single batch status file."""
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        records = []
        i = 0
        
        while i < len(lines):
            line = lines[i]
            
            # Look for the header line to skip
            if 'Workstation' in line and 'Job Stream' in line:
                i += 1
                continue
            
            # Skip empty lines and separator lines
            if not line.strip() or '=' in line or 'SLA BATCH' in line or 'IMS JOB STATUS' in line or 'JOB STATUS' in line:
                i += 1
                continue
            
            # Check if this is a schedule line (contains CPU, #SCHEDULE, time, date, asterisks, STATUS)
            if re.search(r'^[A-Z0-9]+\s+#[A-Z0-9_]+\s+\d{4}\s+\d{2}/\d{2}', line):
                record = self._parse_schedule_block(lines, i)
                if record:
                    records.append(record)
                    # Move to next block
                    i += self._count_block_lines(lines, i)
                else:
                    i += 1
            else:
                i += 1
        
        return records
    
    def _count_block_lines(self, lines: List[str], start_idx: int) -> int:
        """Count how many lines belong to this job block."""
        count = 1  # Schedule line
        idx = start_idx + 1
        
        # Look for dependency line (long spaces followed by text)
        if idx < len(lines) and re.match(r'^\s{100,}', lines[idx]):
            count += 1
            idx += 1
        
        # Look for job line (long spaces followed by job name)
        if idx < len(lines) and re.match(r'^\s{29,}[A-Z0-9]', lines[idx]):
            count += 1
        
        return count
    
    def _parse_schedule_block(self, lines: List[str], idx: int) -> Optional[Dict]:
        """Parse a schedule block (can be 2 or 3 lines)."""
        schedule_line = lines[idx]
        
        # Parse schedule line
        # Format: CPU #SCHEDULE SCHEDTIME SCHEDDATE ******** STATUS PR START ELAPSE [Dependencies]
        parts = schedule_line.split()
        
        if len(parts) < 5:
            return None
        
        cpu = parts[0].strip()
        schedule = parts[1].strip()
        schedtime = parts[2].strip()
        scheddate = parts[3].strip()
        
        # Find the status (SUCC, HOLD, STUCK, ABEND, etc.)
        status = None
        status_idx = None
        for i, part in enumerate(parts):
            if part in ['SUCC', 'HOLD', 'STUCK', 'ABEND', 'READY', 'EXEC']:
                status = part
                status_idx = i
                break
        
        if not status:
            return None
        
        # Extract schedule start and runtime based on status
        schedstart = ''
        schedruntime = ''
        
        if status_idx + 2 < len(parts):
            # Next items after PR (priority)
            next_val = parts[status_idx + 2]
            # Skip if it's [Carry] or other bracketed non-time values
            if next_val.startswith('['):
                pass  # Skip bracketed values
            elif '(' in next_val:
                # HOLD status: PR (START)(RUNTIME)
                schedstart = next_val.strip('()')
                if status_idx + 3 < len(parts):
                    schedruntime = parts[status_idx + 3].strip('()')
            else:
                # SUCC status: PR START RUNTIME
                schedstart = next_val
                if status_idx + 3 < len(parts):
                    schedruntime = parts[status_idx + 3]
        
        # Extract schedule dependencies (everything after the times)
        schedule_dependency = ''
        # Find dependencies starting from the end of the line
        dep_start = schedule_line.find('[', schedule_line.find('********'))
        if dep_start != -1:
            schedule_dependency = schedule_line[dep_start:].strip()
            # Remove [Carry] as it's not a dependency
            schedule_dependency = schedule_dependency.replace('[Carry]', '').strip()
            # Clean up multiple spaces and leading/trailing separators
            schedule_dependency = re.sub(r'\s+', ' ', schedule_dependency)
            schedule_dependency = schedule_dependency.strip(';').strip()
        
        # Check for dependency line (next line with lots of spaces)
        if idx + 1 < len(lines):
            next_line = lines[idx + 1]
            if re.match(r'^\s{100,}', next_line):
                # This is a continuation of schedule dependencies
                schedule_dependency += ' ' + next_line.strip()
                idx += 1
        
        # Parse job line (if exists)
        jobname = ''
        jobstatus = ''
        jobstart = ''
        jobruntime = ''
        job_dependency = ''
        
        if idx + 1 < len(lines):
            job_line = lines[idx + 1]
            # Job line starts with lots of spaces followed by job name
            if re.match(r'^\s{29,}[A-Z0-9]', job_line):
                job_parts = job_line.split()
                if len(job_parts) > 0:
                    jobname = job_parts[0].strip()
                
                # Find job status
                job_status_idx = None
                for i, part in enumerate(job_parts):
                    if part in ['SUCC', 'HOLD', 'STUCK', 'ABEND', 'READY', 'EXEC']:
                        jobstatus = part
                        job_status_idx = i
                        break
                
                if jobstatus and job_status_idx + 2 < len(job_parts):
                    # Next items after PR
                    next_val = job_parts[job_status_idx + 2]
                    if '(' in next_val:
                        # HOLD status: PR (RUNTIME) DEPENDENCY (no start time)
                        jobstart = ''  # No start time for HOLD jobs
                        # Keep parentheses to indicate estimated runtime
                        jobruntime = next_val  # e.g., (00:51)
                        # Dependencies come after runtime for HOLD jobs
                        if job_status_idx + 3 < len(job_parts):
                            remaining_parts = job_parts[job_status_idx + 3:]
                            job_dependency = ' '.join(remaining_parts)
                    else:
                        # SUCC/ABEND status: PR START RUNTIME
                        jobstart = next_val
                        if job_status_idx + 3 < len(job_parts):
                            jobruntime = job_parts[job_status_idx + 3]
                    
                    # Extract job dependencies (only for non-HOLD jobs, as HOLD was already handled)
                    if jobstatus not in ['HOLD'] and not job_dependency:
                        # For SUCC/ABEND: dependencies come after return code and job number
                        # Skip return code (digit) and job number (#J####) to get to dependencies
                        if len(job_parts) > job_status_idx + 4:
                            # Start from after runtime
                            remaining = job_parts[job_status_idx + 4:]
                            # Skip return code if it's a digit
                            if remaining and remaining[0].isdigit():
                                remaining = remaining[1:]
                            # Skip job number if it starts with #
                            if remaining and remaining[0].startswith('#'):
                                remaining = remaining[1:]
                            # What's left is dependencies
                            if remaining:
                                job_dependency = ' '.join(remaining)
        
        return {
            'CPU': cpu,
            'SCHEDULE': schedule,
            'SCHEDDATE': scheddate,
            'JOBNAME': jobname,
            'JOBSTATUS': jobstatus,
            'JOBSTART': self.format_time(jobstart),
            'JOBRUNTIME': jobruntime,
            'JOBDEPENDENCY': job_dependency,
            'SCHEDDEPENDENCY': schedule_dependency,
            'SCHEDTIME': self.format_time(schedtime),
            'SCHEDSTATUS': status,
            'SCHEDSTART': self.format_time(schedstart),
            'SCHEDRUNTIME': schedruntime
        }
    
    def parse_folder(self, folder_path: str) -> List[Dict]:
        """Parse all .txt files in the given folder."""
        from datetime import datetime, timedelta
        
        folder = Path(folder_path)
        all_records = []
        
        # Calculate date range: previous month + first day of current month
        today = datetime.now()
        # First day of current month
        first_of_current_month = today.replace(day=1)
        # Last day of previous month
        last_of_prev_month = first_of_current_month - timedelta(days=1)
        # First day of previous month
        first_of_prev_month = last_of_prev_month.replace(day=1)
        
        # Format as YYYYMMDD for comparison
        start_date = first_of_prev_month.strftime('%Y%m%d')
        end_date = first_of_current_month.strftime('%Y%m%d')
        
        print(f"Filtering files from {first_of_prev_month.strftime('%Y-%m-%d')} to {first_of_current_month.strftime('%Y-%m-%d')}")
        
        for txt_file in folder.glob('SLA_Batch_Status*.txt'):
            # Extract date from filename: SLA_Batch_StatusYYYYMMDD_HHMMSS.txt
            filename = txt_file.stem  # Removes .txt extension
            if 'SLA_Batch_Status' in filename:
                # Extract date part (YYYYMMDD)
                date_part = filename.replace('SLA_Batch_Status', '').split('_')[0]
                if len(date_part) >= 8 and date_part[:8].isdigit():
                    file_date = date_part[:8]
                    # Check if file is within date range
                    if start_date <= file_date <= end_date:
                        print(f"Processing: {txt_file.name}")
                        records = self.parse_file(str(txt_file))
                        all_records.extend(records)
                    else:
                        print(f"Skipping (out of range): {txt_file.name}")
        
        return all_records
    
    def write_csv(self, records: List[Dict], output_file: str):
        """Write parsed records to CSV file."""
        if not records:
            print("No records to write.")
            return
        
        fieldnames = [
            'CPU', 'SCHEDULE', 'SCHEDDATE', 'JOBNAME', 'JOBSTATUS',
            'JOBSTART', 'JOBRUNTIME', 'JOBDEPENDENCY', 'SCHEDDEPENDENCY',
            'SCHEDTIME', 'SCHEDSTATUS', 'SCHEDSTART', 'SCHEDRUNTIME'
        ]
        
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
        
        print(f"CSV file written: {output_file}")
        print(f"Total records: {len(records)}")


def send_email(email_addresses: str, attachment_path: str, subject: str):
    """Send email with attachment using mail command (Linux/Unix).
    
    Args:
        email_addresses: Single email or comma-separated list of emails
        attachment_path: Path to file to attach
        subject: Email subject line
    """
    import subprocess
    import platform
    
    # Split comma-separated emails and clean whitespace
    recipients = [email.strip() for email in email_addresses.split(',')]
    
    if platform.system() == 'Windows':
        print(f"Email sending skipped on Windows. Would send to: {', '.join(recipients)}")
        print(f"Attachment: {attachment_path}")
        return
    
    try:
        # Use mail command with attachment (lowercase -a is more widely supported)
        body = "Please find attached the SLA batch report."
        # Add all recipients to command
        cmd = ['mail', '-s', subject, '-a', attachment_path] + recipients
        
        # Run mail command with body as stdin
        result = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = result.communicate(input=body.encode('utf-8'))
        
        if result.returncode == 0:
            print(f"Email sent successfully to {', '.join(recipients)}")
        else:
            stderr_output = stderr.decode('utf-8') if stderr else ''
            print(f"Failed to send email: {stderr_output}")
    except Exception as e:
        print(f"Error sending email: {e}")


def main():
    """Main execution function."""
    import sys
    import argparse
    from datetime import datetime, timedelta
    import calendar
    import configparser
    
    # Parse command line arguments
    parser_args = argparse.ArgumentParser(
        description='Parse batch job status files and generate SLA report',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python batch_scraper.py /path/to/files
  python batch_scraper.py /path/to/files -o output.xls
  python batch_scraper.py -c config.ini
        """
    )
    parser_args.add_argument('folder', nargs='?', help='Folder path containing batch status files')
    parser_args.add_argument('-o', '--output', help='Output file name (default: SLA-{Month}.xls)')
    parser_args.add_argument('-c', '--config', help='Path to configuration file')
    
    args = parser_args.parse_args()
    
    # Load configuration if provided
    email_address = None
    folder_path = args.folder
    output_file = args.output
    
    if args.config:
        if not os.path.exists(args.config):
            print(f"Error: Config file not found: {args.config}")
            sys.exit(1)
        
        config = configparser.ConfigParser()
        config.read(args.config)
        
        if 'settings' in config:
            if 'folder' in config['settings']:
                folder_path = config['settings']['folder']
            if 'email' in config['settings']:
                email_address = config['settings']['email']
    
    # Validate folder path
    if not folder_path:
        parser_args.print_help()
        sys.exit(1)
    
    if not os.path.exists(folder_path):
        print(f"Error: Folder not found: {folder_path}")
        sys.exit(1)
    
    # Generate default output filename based on previous month
    if not output_file:
        today = datetime.now()
        first_of_current_month = today.replace(day=1)
        last_of_prev_month = first_of_current_month - timedelta(days=1)
        prev_month_name = calendar.month_name[last_of_prev_month.month]
        output_file = f"SLA-{prev_month_name}.csv"
    
    # Process files
    parser = BatchJobParser()
    records = parser.parse_folder(folder_path)
    parser.write_csv(records, output_file)
    
    # Send email if configured
    if email_address:
        today = datetime.now()
        first_of_current_month = today.replace(day=1)
        last_of_prev_month = first_of_current_month - timedelta(days=1)
        prev_month_name = calendar.month_name[last_of_prev_month.month]
        subject = f"SLA Batch Report - {prev_month_name}"
        send_email(email_address, output_file, subject)


if __name__ == '__main__':
    main()
