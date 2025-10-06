"""
DatasetGenerator class for generating synthetic datasets based on specifications.
"""
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from dataset import Dataset

class DatasetGenerator:
    """
    Generates synthetic datasets based on Dataset specifications.
    """
    
    def __init__(self, dataset: Dataset):
        """
        Initialize generator with Dataset specification.
        
        Args:
            dataset: Dataset object containing specifications
        """
        self.dataset = dataset
        self.rng = None
        self.data = {}
        self.categorical_features = set()
        self.datetime_features = set()
        
    def generate(self) -> Path:
        """
        Generate the dataset and save to CSV.
        
        Returns:
            Path to generated CSV file
        """
        # Set random seed if specified
        if self.dataset.random_seed is not None:
            np.random.seed(self.dataset.random_seed)
            self.rng = np.random.RandomState(self.dataset.random_seed)
        else:
            self.rng = np.random.RandomState()
        
        # Track which features are categorical or datetime
        for feature in self.dataset.features:
            if feature['data_type'] == 'categorical':
                self.categorical_features.add(feature['name'])
            if feature.get('distribution', {}).get('type') == 'sequential_datetime':
                self.datetime_features.add(feature['name'])
        
        # Generate features (all as float initially)
        self._generate_features()
        
        # Generate lagged features
        self._generate_lagged_features()
        
        # Apply correlations (before type conversions)
        self._apply_correlations()
        
        # Apply outliers (before categorical conversion, skip categorical features)
        self._apply_outliers()
        
        # Convert int types (before categorical conversion)
        self._convert_int_types()
        
        # Apply categorical conversions
        self._apply_categorical_conversions()
        
        # Apply missing data
        self._apply_missing_data()
        
        # Generate target variable
        self._generate_target()

        # Create DataFrame with only original features and target (exclude lagged features)
        lagged_feature_names = self._get_lagged_feature_names()
        columns_to_include = [col for col in self.data.keys() if col not in lagged_feature_names]
        df = pd.DataFrame({col: self.data[col] for col in columns_to_include})

        # Save to CSV
        output_path = Path("./datasets") / f"{self.dataset.name}.csv"
        df.to_csv(output_path, index=False)

        return output_path

    def _get_lagged_feature_names(self) -> set:
        """Get set of all lagged feature names to exclude from CSV output."""
        lagged_names = set()
        for feature in self.dataset.features:
            name = feature['name']
            lags = feature.get('lags', [])
            for lag in lags:
                lagged_names.add(f"{name}_lag{lag}")
        return lagged_names
    
    def _generate_features(self):
        """Generate all feature variables as float (or object for datetime)."""
        for feature in self.dataset.features:
            name = feature['name']
            distribution = feature['distribution']

            # Generate raw values based on distribution
            values = self._generate_distribution(distribution, self.dataset.n_rows)

            # Keep datetime as object type, convert others to float
            if name in self.datetime_features:
                self.data[name] = values
            else:
                self.data[name] = values.astype(float)
    
    def _generate_lagged_features(self):
        """Generate lagged versions of features that have lags specified."""
        for feature in self.dataset.features:
            name = feature['name']
            lags = feature.get('lags', [])
            
            if lags and name in self.data:
                original_values = self.data[name]
                
                for lag in lags:
                    lag_name = f"{name}_lag{lag}"
                    # Create lagged values with NaN for first 'lag' rows
                    lagged_values = np.full(len(original_values), np.nan)
                    lagged_values[lag:] = original_values[:-lag]
                    self.data[lag_name] = lagged_values
    
    def _generate_distribution(self, dist: Dict[str, Any], n: int) -> np.ndarray:
        """Generate values from specified distribution."""
        dist_type = dist['type']
        
        if dist_type == 'uniform':
            return self.rng.uniform(dist['min'], dist['max'], n)
        
        elif dist_type == 'normal':
            values = self.rng.normal(dist['mean'], dist['std'], n)
            # Apply clipping if specified
            if dist.get('min_clip') is not None:
                values = np.maximum(values, dist['min_clip'])
            if dist.get('max_clip') is not None:
                values = np.minimum(values, dist['max_clip'])
            return values
        
        elif dist_type == 'weibull':
            # Generate Weibull and apply location/scale transformation
            shape = dist['shape']
            scale = dist['scale']
            location = dist.get('location', 0)
            values = location + scale * self.rng.weibull(shape, n)
            return values
        
        elif dist_type == 'random_walk':
            start = dist['start']
            step_size = dist['step_size']
            drift = dist.get('drift', 0)
            
            # Generate random steps
            steps = self.rng.uniform(-step_size, step_size, n) + drift
            # Cumulative sum for random walk
            values = np.cumsum(steps)
            values += start  # Shift to start value
            return values
        
        elif dist_type == 'sequential':
            start = dist['start']
            step = dist['step']
            return np.arange(start, start + n * step, step).astype(float)

        elif dist_type == 'sequential_datetime':
            start_str = dist['start']
            interval = dist['interval']

            # Parse start datetime
            try:
                start_dt = datetime.fromisoformat(start_str)
            except ValueError:
                raise ValueError(f"Invalid datetime format '{start_str}'. Use ISO format (e.g., '2024-01-01' or '2024-01-01T00:00:00')")

            # Generate datetime sequence based on interval
            dates = []
            current_dt = start_dt

            for i in range(n):
                dates.append(current_dt)

                if interval == 'hourly':
                    current_dt = current_dt + timedelta(hours=1)
                elif interval == 'daily':
                    current_dt = current_dt + timedelta(days=1)
                elif interval == 'weekly':
                    current_dt = current_dt + timedelta(weeks=1)
                elif interval == 'monthly':
                    current_dt = current_dt + relativedelta(months=1)
                elif interval == 'quarterly':
                    current_dt = current_dt + relativedelta(months=3)
                elif interval == 'yearly':
                    current_dt = current_dt + relativedelta(years=1)

            # Convert to ISO format strings and return as object array
            return np.array([dt.isoformat() for dt in dates], dtype=object)

        else:
            raise ValueError(f"Unknown distribution type: {dist_type}")
    
    def _apply_correlations(self):
        """Apply correlation structure using Cholesky decomposition."""
        if not self.dataset.correlations:
            return
        
        # Build correlation matrix for correlated variables
        # Group by connected components
        corr_groups = self._get_correlation_groups()
        
        for group_vars, group_corrs in corr_groups:
            if len(group_vars) < 2:
                continue
            
            # Build correlation matrix
            n_vars = len(group_vars)
            corr_matrix = np.eye(n_vars)
            
            for corr in group_corrs:
                var1, var2 = corr['variables']
                idx1 = group_vars.index(var1)
                idx2 = group_vars.index(var2)
                corr_val = corr['correlation']
                corr_matrix[idx1, idx2] = corr_val
                corr_matrix[idx2, idx1] = corr_val
            
            # Get original data (standardized)
            original_data = []
            means = []
            stds = []
            
            for var in group_vars:
                values = self.data[var].astype(float)
                mean = np.mean(values)
                std = np.std(values)
                if std == 0:
                    std = 1  # Avoid division by zero
                means.append(mean)
                stds.append(std)
                standardized = (values - mean) / std
                original_data.append(standardized)
            
            original_data = np.array(original_data).T  # Shape: (n_rows, n_vars)
            
            # Apply Cholesky decomposition
            try:
                L = np.linalg.cholesky(corr_matrix)
            except np.linalg.LinAlgError:
                # Matrix not positive definite, skip this group
                print(f"Warning: Correlation matrix not positive definite for {group_vars}")
                continue
            
            # Generate uncorrelated standard normal
            uncorrelated = self.rng.standard_normal((self.dataset.n_rows, n_vars))
            
            # Apply correlation structure
            correlated = uncorrelated @ L.T
            
            # Transform back to original scale using rank-based method
            for i, var in enumerate(group_vars):
                # Rank-based transformation to preserve marginal distributions
                ranks = np.argsort(np.argsort(correlated[:, i]))
                sorted_original = np.sort(original_data[:, i])
                new_values = sorted_original[ranks]
                
                # Unstandardize
                self.data[var] = new_values * stds[i] + means[i]
    
    def _get_correlation_groups(self) -> List[tuple]:
        """Group correlated variables into connected components."""
        from collections import defaultdict, deque
        
        adj = defaultdict(set)
        all_vars = set()
        
        for corr in self.dataset.correlations:
            var1, var2 = corr['variables']
            adj[var1].add(var2)
            adj[var2].add(var1)
            all_vars.add(var1)
            all_vars.add(var2)
        
        # Find connected components using BFS
        visited = set()
        groups = []
        
        for start_var in all_vars:
            if start_var in visited:
                continue
            
            # BFS to find component
            component = []
            queue = deque([start_var])
            visited.add(start_var)
            
            while queue:
                var = queue.popleft()
                component.append(var)
                
                for neighbor in adj[var]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            
            # Get correlations for this component
            component_corrs = [
                corr for corr in self.dataset.correlations
                if corr['variables'][0] in component and corr['variables'][1] in component
            ]
            
            groups.append((component, component_corrs))
        
        return groups
    
    def _apply_outliers(self):
        """Apply outlier injection to features (only non-categorical, non-datetime)."""
        for feature in self.dataset.features:
            outlier_rate = feature.get('outlier_rate', 0.0)

            # Skip if no outliers, or if categorical or datetime
            if outlier_rate == 0 or feature['name'] in self.categorical_features or feature['name'] in self.datetime_features:
                continue
            
            name = feature['name']
            values = np.array(self.data[name], dtype=float)
            n_outliers = int(self.dataset.n_rows * outlier_rate)
            
            if n_outliers == 0:
                continue
            
            outlier_method = feature.get('outlier_method', 'extreme_high')
            outlier_multiplier = feature.get('outlier_multiplier', 3.0)
            
            # Calculate IQR
            q1 = np.percentile(values, 25)
            q3 = np.percentile(values, 75)
            iqr = q3 - q1
            
            # Select random indices for outliers
            outlier_indices = self.rng.choice(self.dataset.n_rows, n_outliers, replace=False)
            
            # Generate outliers based on method
            if outlier_method == 'extreme_high':
                outlier_values = q3 + outlier_multiplier * iqr
                values[outlier_indices] = outlier_values
            
            elif outlier_method == 'extreme_low':
                outlier_values = q1 - outlier_multiplier * iqr
                values[outlier_indices] = outlier_values
            
            elif outlier_method == 'extreme_both':
                # Split outliers between high and low
                n_high = n_outliers // 2
                n_low = n_outliers - n_high
                
                high_indices = outlier_indices[:n_high]
                low_indices = outlier_indices[n_high:]
                
                values[high_indices] = q3 + outlier_multiplier * iqr
                values[low_indices] = q1 - outlier_multiplier * iqr
            
            self.data[name] = values
    
    def _convert_int_types(self):
        """Convert features with int data_type to integers."""
        for feature in self.dataset.features:
            if feature['data_type'] == 'int' and feature['name'] not in self.categorical_features and feature['name'] not in self.datetime_features:
                name = feature['name']
                values = self.data[name]
                self.data[name] = np.round(values).astype(float)  # Keep as float for NaN support later
    
    def _apply_categorical_conversions(self):
        """Convert numeric values to categorical based on deciles."""
        for feature in self.dataset.features:
            if feature['data_type'] != 'categorical':
                continue
            
            name = feature['name']
            categories = feature['categories']
            values = self.data[name]
            
            # Convert to deciles
            try:
                decile_labels = pd.qcut(values, q=10, labels=False, duplicates='drop')
            except ValueError:
                # If all values are the same, assign to middle category
                decile_labels = np.full(len(values), 4)
            
            # Map deciles to categories
            categorical_values = []
            for label in decile_labels:
                if label is not None and not np.isnan(label):
                    categorical_values.append(categories[int(label)])
                else:
                    categorical_values.append(None)
            
            self.data[name] = categorical_values
    
    def _apply_missing_data(self):
        """Apply missing data to features."""
        for feature in self.dataset.features:
            missing_rate = feature.get('missing_rate', 0.0)
            if missing_rate == 0:
                continue
            
            name = feature['name']
            n_missing = int(self.dataset.n_rows * missing_rate)
            
            if n_missing == 0:
                continue
            
            # Select random indices for missing values
            missing_indices = self.rng.choice(self.dataset.n_rows, n_missing, replace=False)
            
            # Replace with appropriate missing value
            if feature['data_type'] == 'categorical':
                values = list(self.data[name])
                for idx in missing_indices:
                    values[idx] = None
                self.data[name] = values
            else:
                values = np.array(self.data[name], dtype=float)
                values[missing_indices] = np.nan
                self.data[name] = values
    
    def _generate_target(self):
        """Generate target variable from expression."""
        target = self.dataset.target
        name = target['name']
        expression = target['expression']
        data_type = target['data_type']
        noise_percent = target.get('noise_percent', 0.0)
        seasonality_multipliers = target.get('seasonality_multipliers', [])
        
        # Create namespace with feature data and numpy
        # Only include non-categorical, non-datetime features in numeric form
        namespace = {'np': np}
        for feature_name, feature_values in self.data.items():
            if feature_name not in self.categorical_features and feature_name not in self.datetime_features:
                # Convert to array, handling NaN values
                namespace[feature_name] = np.array(feature_values, dtype=float)
            # Categorical and datetime features are not added to namespace (can't use in expressions)
        
        # Evaluate expression
        try:
            target_values = eval(expression, namespace)
        except Exception as e:
            raise ValueError(f"Error evaluating target expression '{expression}': {e}")
        
        # Ensure it's an array
        target_values = np.array(target_values, dtype=float)
        
        # Apply seasonality before noise
        if seasonality_multipliers:
            period = len(seasonality_multipliers)
            # Create seasonality array that repeats for all rows
            seasonality_array = np.array([seasonality_multipliers[i % period] for i in range(len(target_values))])
            target_values = target_values * seasonality_array
        
        # Add noise
        if noise_percent > 0:
            valid_values = target_values[~np.isnan(target_values)]
            if len(valid_values) > 0:
                value_range = np.max(valid_values) - np.min(valid_values)
                if value_range > 0:
                    noise_std = (noise_percent / 100) * value_range
                    noise = self.rng.normal(0, noise_std, len(target_values))
                    target_values = target_values + noise
        
        # Convert to appropriate type
        if data_type == 'int':
            target_values = np.round(target_values).astype(float)  # Keep as float for NaN support
        elif data_type == 'categorical':
            # Convert to deciles and map to categories
            categories = target['categories']
            
            # Handle NaN values in qcut
            valid_mask = ~np.isnan(target_values)
            categorical_values = [None] * len(target_values)
            
            if np.sum(valid_mask) > 0:
                valid_values = target_values[valid_mask]
                try:
                    decile_labels = pd.qcut(valid_values, q=10, labels=False, duplicates='drop')
                except ValueError:
                    # All values the same
                    decile_labels = np.full(len(valid_values), 4)
                
                valid_idx = 0
                for i in range(len(target_values)):
                    if valid_mask[i]:
                        label = decile_labels[valid_idx]
                        categorical_values[i] = categories[int(label)] if label is not None else None
                        valid_idx += 1
            
            target_values = categorical_values
        
        # Apply outliers to target if specified
        outlier_rate = target.get('outlier_rate', 0.0)
        if outlier_rate > 0 and data_type != 'categorical':
            n_outliers = int(self.dataset.n_rows * outlier_rate)
            if n_outliers > 0:
                outlier_method = target.get('outlier_method', 'extreme_high')
                outlier_multiplier = target.get('outlier_multiplier', 3.0)
                
                values_array = np.array(target_values, dtype=float)
                valid_values = values_array[~np.isnan(values_array)]
                
                if len(valid_values) > 0:
                    q1 = np.percentile(valid_values, 25)
                    q3 = np.percentile(valid_values, 75)
                    iqr = q3 - q1
                    
                    outlier_indices = self.rng.choice(self.dataset.n_rows, n_outliers, replace=False)
                    
                    if outlier_method == 'extreme_high':
                        values_array[outlier_indices] = q3 + outlier_multiplier * iqr
                    elif outlier_method == 'extreme_low':
                        values_array[outlier_indices] = q1 - outlier_multiplier * iqr
                    elif outlier_method == 'extreme_both':
                        n_high = n_outliers // 2
                        n_low = n_outliers - n_high
                        values_array[outlier_indices[:n_high]] = q3 + outlier_multiplier * iqr
                        values_array[outlier_indices[n_high:]] = q1 - outlier_multiplier * iqr
                    
                    target_values = values_array
        
        # Apply missing data to target if specified
        missing_rate = target.get('missing_rate', 0.0)
        if missing_rate > 0:
            n_missing = int(self.dataset.n_rows * missing_rate)
            if n_missing > 0:
                missing_indices = self.rng.choice(self.dataset.n_rows, n_missing, replace=False)
                
                if data_type == 'categorical':
                    target_values = list(target_values)
                    for idx in missing_indices:
                        target_values[idx] = None
                else:
                    target_values = np.array(target_values, dtype=float)
                    target_values[missing_indices] = np.nan
        
        self.data[name] = target_values
        